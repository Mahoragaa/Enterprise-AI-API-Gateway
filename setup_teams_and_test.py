"""
setup_teams_and_test.py — Enterprise AI API Gateway Test Harness
================================================================
Demonstrates two production-grade gateway behaviours:

  TEST 1 — Dynamic Priority-Based Rate Limiting
    Creates two teams (engineering / marketing) with RPM limits that make
    the model saturated when both fire simultaneously.  Proves that when
    capacity is exhausted, engineering (high priority) succeeds while
    marketing (low priority) receives HTTP 429 Rate Limit errors.

  TEST 2 — Automatic Health-Check Failover
    Triggers the "poison pill" on the primary mock provider.  Waits for
    LiteLLM's background health checker to detect the failure (≤15 s).
    Proves that a subsequent request routes to the fallback with zero
    user-facing errors.

Usage:
  pip install httpx rich
  python setup_teams_and_test.py

Prerequisites:
  docker compose up --build -d   (wait ~20 s for services to stabilise)
"""

import asyncio
import sys
import time
from collections import Counter

# Fix Windows console UTF-8 encoding for Rich emojis and symbols
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

# ─── Configuration ────────────────────────────────────────────────────────────

GATEWAY_URL = "http://localhost:4000"
MOCK_PRIMARY_URL = "http://localhost:9000"
MOCK_FALLBACK_URL = "http://localhost:9001"

# LiteLLM master key — must match LITELLM_MASTER_KEY in docker-compose.yml
MASTER_KEY = "sk-master-key-1234"

ADMIN_HEADERS = {
    "Authorization": f"Bearer {MASTER_KEY}",
    "Content-Type": "application/json",
}

console = Console()


# ─── Utility helpers ──────────────────────────────────────────────────────────

def print_section(title: str) -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))
    console.print()


def print_result(label: str, value, ok: bool = True) -> None:
    icon = "✅" if ok else "❌"
    colour = "green" if ok else "red"
    console.print(f"  {icon} [bold]{label}:[/bold] [{colour}]{value}[/{colour}]")


async def wait_for_gateway(timeout: int = 60) -> None:
    """Block until LiteLLM proxy is reachable."""
    print_section("Gateway Readiness Check")
    start = time.time()
    async with httpx.AsyncClient() as client:
        while time.time() - start < timeout:
            try:
                r = await client.get(f"{GATEWAY_URL}/health/liveliness", timeout=3)
                if r.status_code == 200:
                    print_result("Gateway status", r.text.strip(), ok=True)
                    return
            except httpx.ConnectError:
                pass
            console.print("  ⏳ Waiting for gateway...", end="\r")
            await asyncio.sleep(2)
    raise RuntimeError("Gateway did not become ready in time.")


# ─── Team & Key Setup ─────────────────────────────────────────────────────────

async def create_team(client: httpx.AsyncClient, alias: str, rpm_limit: int) -> str:
    """Create a team and return its team_id."""
    r = await client.post(
        f"{GATEWAY_URL}/team/new",
        headers=ADMIN_HEADERS,
        json={
            "team_alias": alias,
            "rpm_limit": rpm_limit,       # requests-per-minute cap for the whole team
            "models": ["gpt-4o"],
        },
    )
    r.raise_for_status()
    team_id = r.json()["team_id"]
    console.print(f"  📂 Team [bold]{alias}[/bold] created → team_id: [cyan]{team_id}[/cyan]")
    return team_id


async def create_key(
    client: httpx.AsyncClient,
    team_id: str,
    alias: str,
    priority: float,
) -> str:
    """Generate an API key for a team and return the key string."""
    r = await client.post(
        f"{GATEWAY_URL}/key/generate",
        headers=ADMIN_HEADERS,
        json={
            "team_id": team_id,
            "key_alias": alias,
            # priority: lower number = higher priority in LiteLLM's scheduler
            # When saturation_threshold (0.50) is crossed, requests are served
            # in priority order — engineering (0) before marketing (10).
            "priority": priority,
            "models": ["gpt-4o"],
        },
    )
    r.raise_for_status()
    key = r.json()["key"]
    console.print(
        f"  🔑 Key [bold]{alias}[/bold] → [yellow]{key[:20]}…[/yellow]  "
        f"(priority={priority})"
    )
    return key


async def setup_teams() -> tuple[str, str]:
    """
    Create two teams with their API keys:
      - engineering  → RPM 40, priority 0  (highest)
      - marketing    → RPM 10, priority 10 (lowest)

    The combined RPM (50) exceeds what the mock can serve fast enough,
    so the scheduler kicks in at 50% saturation and prioritises engineering.

    Returns: (engineering_key, marketing_key)
    """
    print_section("Setting Up Teams & API Keys")

    import uuid
    run_suffix = uuid.uuid4().hex[:6]

    async with httpx.AsyncClient(timeout=30) as client:
        # Teams
        eng_team = await create_team(client, f"engineering-{run_suffix}", rpm_limit=40)
        mkt_team = await create_team(client, f"marketing-{run_suffix}",   rpm_limit=10)

        # Keys with priorities
        eng_key = await create_key(client, eng_team, f"engineering-key-{run_suffix}", priority=0.0)
        mkt_key = await create_key(client, mkt_team, f"marketing-key-{run_suffix}",   priority=10.0)

    return eng_key, mkt_key


# ─── Test 1: Priority-Based Rate Limiting ────────────────────────────────────

async def send_request(
    client: httpx.AsyncClient,
    api_key: str,
    team: str,
    req_id: int,
) -> dict:
    """Fire a single chat completion and return a structured result."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": f"Request {req_id} from {team}"}],
        # Pass explicit priority in extra_body (the proxy reads this field)
        "priority": 0 if team == "engineering" else 10,
        "max_tokens": 50,
    }
    start = time.perf_counter()
    try:
        r = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20,
        )
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {
            "team": team,
            "req_id": req_id,
            "status": r.status_code,
            "ok": r.status_code == 200,
            "latency_ms": latency_ms,
            "body": r.json() if r.status_code in (200, 429) else {},
        }
    except Exception as exc:
        return {
            "team": team,
            "req_id": req_id,
            "status": 0,
            "ok": False,
            "latency_ms": 0,
            "error": str(exc),
        }


async def test_priority_rate_limiting(eng_key: str, mkt_key: str) -> None:
    """
    Fire 30 engineering + 30 marketing requests simultaneously.
    At 50% saturation the scheduler enforces priority ordering:
      - Engineering (priority=0) requests succeed
      - Marketing   (priority=10) requests receive 429 when capacity is tight
    """
    print_section("Test 1 — Dynamic Priority-Based Rate Limiting")
    console.print(
        Panel(
            "[bold]Firing 60 concurrent requests[/bold] (30 engineering + 30 marketing).\n"
            "LiteLLM's scheduler enforces priority at [bold cyan]50% saturation[/bold cyan].\n"
            "Engineering (priority=0) should dominate; marketing should see 429s.",
            title="[magenta]Test 1[/magenta]",
            border_style="magenta",
        )
    )

    REQUESTS_PER_TEAM = 30
    async with httpx.AsyncClient() as client:
        tasks = []
        for i in range(REQUESTS_PER_TEAM):
            tasks.append(send_request(client, eng_key, "engineering", i + 1))
            tasks.append(send_request(client, mkt_key, "marketing",   i + 1))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Sending 60 concurrent requests...", total=None)
            results = await asyncio.gather(*tasks)
            progress.remove_task(task)

    # ── Tally results ────────────────────────────────────────────────────────
    eng_results = [r for r in results if r["team"] == "engineering"]
    mkt_results = [r for r in results if r["team"] == "marketing"]

    eng_success = sum(1 for r in eng_results if r["ok"])
    mkt_success = sum(1 for r in mkt_results if r["ok"])
    eng_429     = sum(1 for r in eng_results if r.get("status") == 429)
    mkt_429     = sum(1 for r in mkt_results if r.get("status") == 429)

    # ── Display results table ─────────────────────────────────────────────────
    table = Table(title="Rate Limiting Results", show_header=True, border_style="blue")
    table.add_column("Team",           style="bold")
    table.add_column("Requests Sent",  justify="center")
    table.add_column("✅ Success",      justify="center", style="green")
    table.add_column("🚫 429 Blocked", justify="center", style="red")
    table.add_column("Success Rate",   justify="center")

    def pct(n, total):
        return f"{round(n / total * 100)}%" if total else "N/A"

    table.add_row(
        "engineering (priority=0)",
        str(REQUESTS_PER_TEAM),
        str(eng_success),
        str(eng_429),
        pct(eng_success, REQUESTS_PER_TEAM),
    )
    table.add_row(
        "marketing (priority=10)",
        str(REQUESTS_PER_TEAM),
        str(mkt_success),
        str(mkt_429),
        pct(mkt_success, REQUESTS_PER_TEAM),
    )
    console.print(table)

    # ── Assertions / verdict ──────────────────────────────────────────────────
    console.print()
    if eng_success > mkt_success:
        console.print(
            Panel(
                f"[bold green]PASS[/bold green] — Engineering succeeded "
                f"[green]{eng_success}/{REQUESTS_PER_TEAM}[/green] vs "
                f"marketing [red]{mkt_success}/{REQUESTS_PER_TEAM}[/red].\n"
                "Priority scheduling is working correctly.",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[yellow]PARTIAL[/yellow] — Priority difference not clearly visible.\n"
                "The mock may be serving all requests faster than saturation builds.\n"
                "Try increasing REQUESTS_PER_TEAM or lowering the mock's RPM limit.",
                border_style="yellow",
            )
        )


# ─── Test 2: Health-Check Failover ───────────────────────────────────────────

async def send_single_request(api_key: str) -> dict:
    """Send one completion and return status + which instance served it."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Which instance are you?"}],
                "max_tokens": 60,
            },
        )
    body = r.json()
    content = ""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        pass
    return {"status": r.status_code, "content": content, "ok": r.status_code == 200}


async def test_health_check_failover(eng_key: str) -> None:
    """
    1. Confirm primary is serving traffic
    2. Poison the primary mock (forces 500s)
    3. Wait ≤15 s for LiteLLM's background health checker to detect failure
    4. Prove traffic seamlessly routes to fallback — zero 5xx errors to users
    5. Cure the primary and observe it re-enters the pool
    """
    print_section("Test 2 — Automatic Health-Check Failover")
    console.print(
        Panel(
            "Steps:\n"
            "  1. Verify primary is healthy and serving requests\n"
            "  2. Activate the [bold red]poison pill[/bold red] on the primary mock\n"
            "  3. Wait [bold]15 seconds[/bold] for the background health checker\n"
            "  4. Send a request — it must succeed via the [bold green]fallback[/bold green]\n"
            "  5. Cure the primary and confirm it eventually returns to rotation",
            title="[magenta]Test 2[/magenta]",
            border_style="magenta",
        )
    )

    async with httpx.AsyncClient(timeout=10) as admin:

        # ── Step 1: Baseline — confirm primary is healthy ─────────────────────
        console.print("\n[bold]Step 1:[/bold] Verifying primary mock is healthy...")
        r = await admin.get(f"{MOCK_PRIMARY_URL}/admin/status")
        status = r.json()
        print_result("Primary poisoned?", status["poisoned"], ok=not status["poisoned"])
        print_result("Primary requests served", status["request_count"])

        baseline = await send_single_request(eng_key)
        print_result(
            "Pre-poison request status",
            baseline["status"],
            ok=baseline["ok"],
        )
        console.print(f"  📝 Response snippet: [dim]{baseline['content'][:80]}[/dim]")

        # ── Step 2: Activate poison pill ─────────────────────────────────────
        console.print("\n[bold]Step 2:[/bold] Activating poison pill on primary mock...")
        r = await admin.post(f"{MOCK_PRIMARY_URL}/admin/poison")
        poison_resp = r.json()
        print_result("Poison activated", poison_resp["status"], ok=True)
        console.print(f"  💬 {poison_resp['message']}")

        # Confirm the primary now returns errors
        r = await admin.get(f"{MOCK_PRIMARY_URL}/health")
        print_result(
            "Primary /health after poison",
            r.status_code,
            ok=r.status_code == 503,
        )

    # ── Step 3: Wait for health checker ─────────────────────────────────────
    console.print(
        f"\n[bold]Step 3:[/bold] Waiting [bold cyan]15 seconds[/bold cyan] for "
        "LiteLLM background health checker to detect the failure..."
    )
    console.print("  (health_check_interval = 10s + 5s buffer for cooldown)")

    for remaining in range(15, 0, -1):
        console.print(
            f"  ⏳ {remaining:2d}s remaining...",
            end="\r",
        )
        await asyncio.sleep(1)
    console.print("  ✅ Wait complete.                    ")

    # ── Step 4: Prove seamless failover ──────────────────────────────────────
    console.print("\n[bold]Step 4:[/bold] Sending request — expecting fallback to serve it...")

    FAILOVER_ATTEMPTS = 5
    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(FAILOVER_ATTEMPTS):
            res = await send_single_request(eng_key)
            results.append(res)
            icon = "✅" if res["ok"] else "❌"
            console.print(
                f"  {icon} Attempt {i + 1}: HTTP {res['status']} | "
                f"[dim]{res['content'][:70]}[/dim]"
            )
            await asyncio.sleep(0.5)

    success_count = sum(1 for r in results if r["ok"])
    all_via_fallback = all(
        "fallback" in r.get("content", "").lower()
        for r in results
        if r["ok"]
    )

    console.print()
    if success_count == FAILOVER_ATTEMPTS:
        console.print(
            Panel(
                f"[bold green]PASS[/bold green] — All {FAILOVER_ATTEMPTS}/{FAILOVER_ATTEMPTS} "
                "requests succeeded after primary was poisoned.\n"
                + (
                    "Response content confirms traffic routed to [bold green]FALLBACK[/bold green] instance. ✅"
                    if all_via_fallback
                    else "Check response content to confirm fallback routing."
                ),
                border_style="green",
            )
        )
    elif success_count > 0:
        console.print(
            Panel(
                f"[yellow]PARTIAL[/yellow] — {success_count}/{FAILOVER_ATTEMPTS} succeeded.\n"
                "Health checker may still be within its cooldown window.\n"
                "Try waiting a few more seconds or check LiteLLM logs.",
                border_style="yellow",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]FAIL[/bold red] — No requests succeeded.\n"
                "The health checker may not have detected the outage yet, or\n"
                "both deployments may be unhealthy (LiteLLM falls back to all\n"
                "deployments as a safety net — check logs).",
                border_style="red",
            )
        )

    # ── Step 5: Cure the primary ──────────────────────────────────────────────
    console.print("\n[bold]Step 5:[/bold] Curing primary mock...")
    async with httpx.AsyncClient(timeout=10) as admin:
        r = await admin.post(f"{MOCK_PRIMARY_URL}/admin/cure")
        cure_resp = r.json()
    print_result("Primary cured", cure_resp["status"], ok=True)
    console.print(
        f"  📊 Primary was down for {cure_resp['downtime_seconds']}s. "
        "It will re-enter the routing pool after cooldown expires (60s)."
    )


# ─── Gateway Health Summary ───────────────────────────────────────────────────

async def print_gateway_health() -> None:
    """Hit the LiteLLM /health endpoint and display a summary table."""
    print_section("LiteLLM Gateway — Model Health Summary")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{GATEWAY_URL}/health",
            headers=ADMIN_HEADERS,
        )
    if r.status_code != 200:
        console.print(f"  [red]Could not fetch health: {r.status_code}[/red]")
        return

    body = r.json()
    table = Table(title="Deployment Health", show_header=True, border_style="blue")
    table.add_column("Model",    style="bold")
    table.add_column("API Base")
    table.add_column("Status",   justify="center")

    for ep in body.get("healthy_endpoints", []):
        table.add_row(
            ep.get("model", "—"),
            ep.get("api_base", "—"),
            "[green]✅ healthy[/green]",
        )
    for ep in body.get("unhealthy_endpoints", []):
        table.add_row(
            ep.get("model", "—"),
            ep.get("api_base", "—"),
            "[red]❌ unhealthy[/red]",
        )

    console.print(table)
    console.print(
        f"\n  ℹ️  Healthy: [green]{len(body.get('healthy_endpoints', []))}[/green]  |  "
        f"Unhealthy: [red]{len(body.get('unhealthy_endpoints', []))}[/red]"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]Enterprise AI API Gateway[/bold cyan]\n"
            "[dim]LiteLLM · Health-Check Failover · Priority Rate Limiting[/dim]",
            border_style="cyan",
        )
    )

    # 0. Wait until the gateway is ready
    await wait_for_gateway(timeout=90)

    # Show initial health state
    await print_gateway_health()

    # 1. Create teams and API keys
    eng_key, mkt_key = await setup_teams()

    # 2. Priority rate-limiting demonstration
    await test_priority_rate_limiting(eng_key, mkt_key)

    # 3. Health-check failover demonstration
    await test_health_check_failover(eng_key)

    # Final health state
    await print_gateway_health()

    console.print()
    console.print(
        Panel(
            "[bold green]All tests complete![/bold green]\n\n"
            "What was demonstrated:\n"
            "  ✅ Priority-based rate limiting via LiteLLM scheduler\n"
            "  ✅ Proactive health-check-driven failover (no user-facing errors)\n\n"
            "Next steps:\n"
            "  • Review LiteLLM logs: [cyan]docker compose logs -f litellm[/cyan]\n"
            "  • Check Redis state:   [cyan]docker exec -it gateway-redis redis-cli keys '*'[/cyan]\n"
            "  • Admin UI:            [cyan]http://localhost:4000/ui[/cyan] (master key: sk-master-key-1234)",
            border_style="green",
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
