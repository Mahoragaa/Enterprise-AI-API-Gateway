"""
load_test/locustfile.py — Enterprise AI API Gateway Load Test
=============================================================
Simulates realistic multi-team concurrent traffic to demonstrate:
  1. Priority enforcement under saturation
  2. Health-check failover behaviour under load

Usage:
  # Start the gateway stack first:
  docker compose up --build -d

  # Run with the Locust web UI (visit http://localhost:8089):
  locust -f load_test/locustfile.py --host http://localhost:4000

  # Run headlessly (CI / quick smoke):
  locust -f load_test/locustfile.py \
    --host http://localhost:4000 \
    --headless \
    --users 50 \
    --spawn-rate 5 \
    --run-time 60s \
    --csv load_test/results

Teams are defined in TEAM_CONFIGS below.  Keys are generated at startup
via the LiteLLM admin API — no manual setup needed.
"""

import random
import time

import httpx
from locust import HttpUser, between, events, task
from locust.runners import MasterRunner, WorkerRunner

# ─── Config ──────────────────────────────────────────────────────────────────

GATEWAY_URL    = "http://localhost:4000"
MASTER_KEY     = "sk-master-key-1234"
ADMIN_HEADERS  = {
    "Authorization": f"Bearer {MASTER_KEY}",
    "Content-Type": "application/json",
}

# (team_alias, rpm_limit, priority, weight)
# weight = relative proportion of simulated users
TEAM_CONFIGS = [
    ("engineering", 100, 0,  3),   # high priority, 3× more users
    ("marketing",   25,  10, 1),   # low priority
]

# Populated at startup by the on_test_start hook
TEAM_KEYS: dict[str, str] = {}


# ─── Startup: create teams + keys ────────────────────────────────────────────

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """
    Runs once before load generation begins.
    Creates all teams and their API keys via the LiteLLM admin API.
    """
    if isinstance(environment.runner, WorkerRunner):
        return  # only master/standalone creates teams

    print("\n🔧 Creating teams and API keys...")
    with httpx.Client(timeout=30) as client:
        for alias, rpm, priority, _ in TEAM_CONFIGS:
            # Create team
            r = client.post(
                f"{GATEWAY_URL}/team/new",
                headers=ADMIN_HEADERS,
                json={"team_alias": f"locust-{alias}", "rpm_limit": rpm, "models": ["gpt-4o"]},
            )
            r.raise_for_status()
            team_id = r.json()["team_id"]

            # Create key
            r = client.post(
                f"{GATEWAY_URL}/key/generate",
                headers=ADMIN_HEADERS,
                json={
                    "team_id": team_id,
                    "key_alias": f"locust-{alias}-key",
                    "priority": float(priority),
                    "models": ["gpt-4o"],
                },
            )
            r.raise_for_status()
            TEAM_KEYS[alias] = r.json()["key"]
            print(f"  ✅ {alias}: key={TEAM_KEYS[alias][:20]}…  priority={priority}")

    print("✅ Teams ready. Starting load generation...\n")


# ─── User Classes ─────────────────────────────────────────────────────────────

PROMPTS = [
    "Summarise the key trends in enterprise AI adoption.",
    "Write a two-sentence executive summary for a quarterly report.",
    "What are the top 3 risks of deploying LLMs in production?",
    "Explain rate limiting in one paragraph.",
    "Generate a Python function to parse ISO 8601 dates.",
]


class EngineeringUser(HttpUser):
    """
    High-priority team.  Fires chat completions with priority=0.
    Expected: high success rate even under saturation.
    """
    wait_time = between(0.5, 1.5)
    weight = 3   # 3× more engineering users than marketing

    def on_start(self):
        self.api_key = TEAM_KEYS.get("engineering", MASTER_KEY)

    @task
    def chat_completion(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
            "priority": 0,       # highest priority
            "max_tokens": 80,
        }
        with self.client.post(
            "/v1/chat/completions",
            headers=headers,
            json=payload,
            name="/v1/chat/completions [engineering]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.failure(f"Rate limited (429) — unexpected for engineering")
            else:
                resp.failure(f"HTTP {resp.status_code}")


class MarketingUser(HttpUser):
    """
    Low-priority team.  Fires chat completions with priority=10.
    Expected: receives 429s when the model is saturated.
    """
    wait_time = between(1.0, 2.0)
    weight = 1

    def on_start(self):
        self.api_key = TEAM_KEYS.get("marketing", MASTER_KEY)

    @task(3)
    def chat_completion(self):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
            "priority": 10,      # lowest priority
            "max_tokens": 80,
        }
        with self.client.post(
            "/v1/chat/completions",
            headers=headers,
            json=payload,
            name="/v1/chat/completions [marketing]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                # This is *expected* for marketing under saturation — mark as success
                # so it doesn't pollute the failure chart, but tag the name
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def health_check(self):
        """Lightweight health probe — simulates uptime monitoring."""
        with self.client.get(
            "/health/liveliness",
            name="/health/liveliness",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Gateway unhealthy: {resp.status_code}")
