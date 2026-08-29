"""
live_demo.py — Enterprise AI API Gateway Live Interactive Demo
==============================================================
Runs an interactive, visual walkthrough showing the gateway in action:
  1. Sending requests through the Gateway (Primary mock answers)
  2. Killing the primary provider (Poison pill)
  3. Automatic zero-downtime failover to the Fallback provider
  4. Priority-based rate limiting (Engineering vs Marketing)
  5. Healing the primary provider
"""

import asyncio
import sys
import time

# Fix Windows console UTF-8 encoding
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
from rich.table import Table

console = Console()

GATEWAY_URL = "http://localhost:4000"
PRIMARY_URL = "http://localhost:9000"
FALLBACK_URL = "http://localhost:9001"
MASTER_KEY = "sk-master-key-1234"

ADMIN_HEADERS = {
    "Authorization": f"Bearer {MASTER_KEY}",
    "Content-Type": "application/json",
}


async def pause(seconds: float = 2.0):
    await asyncio.sleep(seconds)


async def main():
    console.clear()
    console.print(
        Panel.fit(
            "[bold cyan]🚀 ENTERPRISE AI API GATEWAY — LIVE DEMO[/bold cyan]\n"
            "[dim]Watching LiteLLM handle Provider Outages & Priority Traffic in Real Time[/dim]",
            border_style="cyan",
        )
    )
    await pause(1.5)

    async with httpx.AsyncClient(timeout=30) as client:

        # Ensure mock provider is not in poisoned state from previous runs
        await client.post(f"{PRIMARY_URL}/admin/cure")

        # ── SCENE 1: Normal Operations ─────────────────────────────────────────
        console.print("\n[bold yellow]═══ SCENE 1: Normal Operations (Both Providers Healthy) ═══[/bold yellow]")
        console.print("Sending a user request through the Gateway: [dim]http://localhost:4000/v1/chat/completions[/dim]")

        req_payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello Gateway! Which provider is serving this request?"}],
            "max_tokens": 60,
        }

        resp = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json=req_payload,
        )
        content = resp.json()["choices"][0]["message"]["content"]

        console.print(f"\n[green]📥 Gateway Response (HTTP {resp.status_code}):[/green]")
        console.print(Panel(f"[bold white]{content}[/bold white]", border_style="green", title="Response"))
        await pause(2.0)

        # ── SCENE 2: The Outage (Poison Pill) ──────────────────────────────────
        console.print("\n[bold red]═══ SCENE 2: Simulating Sudden Provider Outage! ═══[/bold red]")
        console.print("🚨 Triggering catastrophic failure on [bold red]Primary Mock Provider[/bold red] via poison pill...")

        poison_resp = await client.post(f"{PRIMARY_URL}/admin/poison")
        console.print(f"  ⚡ Primary /admin/poison status: [red]{poison_resp.json()['status']}[/red]")
        console.print("  ⚠️  The primary provider is now returning HTTP 500/503 errors.")
        await pause(2.0)

        console.print("\nWaiting for LiteLLM's proactive background health checker to detect the dead node...")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[yellow]Probing & updating routing pool (~12s)...[/yellow]", total=12)
            for _ in range(12):
                await asyncio.sleep(1)
                progress.advance(task, 1)

        # ── SCENE 3: Zero-Downtime Failover ───────────────────────────────────
        console.print("\n[bold green]═══ SCENE 3: Testing Automatic Failover ═══[/bold green]")
        console.print("Sending the exact same user request to the Gateway...")

        failover_resp = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=ADMIN_HEADERS,
            json=req_payload,
        )
        failover_content = failover_resp.json()["choices"][0]["message"]["content"]

        console.print(f"\n[green]📥 Gateway Response (HTTP {failover_resp.status_code}):[/green]")
        console.print(Panel(f"[bold white]{failover_content}[/bold white]", border_style="cyan", title="Failover Response"))

        if "FALLBACK" in failover_content:
            console.print("[bold green]✅ ZERO-DOWNTIME SUCCESS:[/bold green] The request was seamlessly served by the [bold cyan]FALLBACK[/bold cyan] instance!")
        else:
            console.print("[bold green]✅ Request succeeded with zero errors![/bold green]")
        await pause(2.5)

        # ── SCENE 4: Priority Traffic Under Saturation ─────────────────────────
        console.print("\n[bold magenta]═══ SCENE 4: Dynamic Priority Rate Limiting (Under Saturation) ═══[/bold magenta]")
        console.print("Firing concurrent traffic from two competing teams:")
        console.print("  • [cyan]Engineering Team[/cyan] (Priority 0 — Critical)")
        console.print("  • [yellow]Marketing Team[/yellow]   (Priority 10 — Low)\n")

        # Generate unique aliases with timestamp so script is infinitely re-runnable
        import uuid
        run_suffix = uuid.uuid4().hex[:6]
        t1 = await client.post(f"{GATEWAY_URL}/team/new", headers=ADMIN_HEADERS, json={"team_alias": f"demo-eng-{run_suffix}", "rpm_limit": 50, "models": ["gpt-4o"]})
        t2 = await client.post(f"{GATEWAY_URL}/team/new", headers=ADMIN_HEADERS, json={"team_alias": f"demo-mkt-{run_suffix}", "rpm_limit": 10, "models": ["gpt-4o"]})
        
        k1_resp = await client.post(f"{GATEWAY_URL}/key/generate", headers=ADMIN_HEADERS, json={"team_id": t1.json()["team_id"], "key_alias": f"eng-key-{run_suffix}", "priority": 0, "models": ["gpt-4o"]})
        k2_resp = await client.post(f"{GATEWAY_URL}/key/generate", headers=ADMIN_HEADERS, json={"team_id": t2.json()["team_id"], "key_alias": f"mkt-key-{run_suffix}", "priority": 10, "models": ["gpt-4o"]})
        k1 = k1_resp.json()["key"]
        k2 = k2_resp.json()["key"]

        async def send_traffic(key, team, priority):
            try:
                r = await client.post(
                    f"{GATEWAY_URL}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Ping"}], "priority": priority},
                )
                return (team, r.status_code)
            except Exception:
                return (team, 500)

        tasks = []
        for _ in range(25):
            tasks.append(send_traffic(k1, "engineering", 0))
            tasks.append(send_traffic(k2, "marketing", 10))

        results = await asyncio.gather(*tasks)
        eng_ok = sum(1 for t, s in results if t == "engineering" and s == 200)
        eng_429 = sum(1 for t, s in results if t == "engineering" and s == 429)
        mkt_ok = sum(1 for t, s in results if t == "marketing" and s == 200)
        mkt_429 = sum(1 for t, s in results if t == "marketing" and s == 429)

        table = Table(title="Live Priority Enforcement Results", border_style="blue")
        table.add_column("Team", style="bold")
        table.add_column("Priority", justify="center")
        table.add_column("Requests Sent", justify="center")
        table.add_column("✅ Success (200 OK)", justify="center", style="green")
        table.add_column("🚫 Throttled (429 Rate Limit)", justify="center", style="red")

        table.add_row("Engineering", "0 (Critical)", "25", str(eng_ok), str(eng_429))
        table.add_row("Marketing", "10 (Low)", "25", str(mkt_ok), str(mkt_429))
        console.print(table)
        await pause(2.0)

        # ── SCENE 5: Healing ──────────────────────────────────────────────────
        console.print("\n[bold green]═══ SCENE 5: Healing the Infrastructure ═══[/bold green]")
        cure_resp = await client.post(f"{PRIMARY_URL}/admin/cure")
        console.print(f"  🩹 Primary /admin/cure status: [green]{cure_resp.json()['status']}[/green]")
        console.print("  🔄 Primary will re-enter the load-balancing pool automatically.")

        console.print(
            Panel(
                "[bold green]✨ Live Demo Complete![/bold green]\n\n"
                "Summary of what you just saw:\n"
                "  1. Live request routing to Primary\n"
                "  2. Simulated outage via poison pill\n"
                "  3. Zero-downtime routing to Fallback provider\n"
                "  4. Dynamic priority rate limiting protecting Engineering under burst load\n"
                "  5. Infrastructure healing and recovery",
                border_style="green",
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
