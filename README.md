# Enterprise AI API Gateway

A production-grade LLM proxy built on **LiteLLM** demonstrating:

- 🏥 **Proactive health-check-driven routing** — dead endpoints are removed before users hit errors
- ⚡ **Dynamic priority-based rate limiting** — critical teams get bandwidth when traffic is high

## Project Structure

```
Enterprise AI API Gateway/
├── docker-compose.yml          # Orchestrates all 4 services
├── litellm_config.yaml         # LiteLLM proxy config (health + priority settings)
├── setup_teams_and_test.py     # Full test harness (teams, rate limiting, failover)
└── mock_provider/
    ├── Dockerfile              # Lightweight Python image
    └── main.py                 # FastAPI mock with poison-pill control plane
```

## Quick Start

```bash
# 1. Start the stack
docker compose up --build -d

# 2. Wait ~20 seconds for services to stabilise, then run the tests
pip install httpx rich
python setup_teams_and_test.py
```

## Services

| Service | Host Port | Purpose |
|---|---|---|
| `litellm` | `4000` | The API Gateway proxy |
| `mock-api-primary` | `9000` | Simulated primary LLM provider |
| `mock-api-fallback` | `9001` | Simulated fallback LLM provider |
| `redis` | `6379` | Distributed rate limiting & health state |

## Manual Testing

### Trigger a provider outage
```bash
# Poison the primary mock
curl -X POST http://localhost:9000/admin/poison

# Watch LiteLLM detect it and fail over (check logs)
docker compose logs -f litellm

# Send a request — should succeed via fallback
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-master-key-1234" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}'

# Restore the primary
curl -X POST http://localhost:9000/admin/cure
```

### Inspect deployment health
```bash
curl http://localhost:4000/health \
  -H "Authorization: Bearer sk-master-key-1234" | python -m json.tool
```

### Admin UI
Open http://localhost:4000/ui and log in with master key `sk-master-key-1234`.

## Configuration Highlights

### Health-Check Routing (`litellm_config.yaml`)
```yaml
general_settings:
  background_health_checks: true
  health_check_interval: 10         # probe every 10 seconds
  enable_health_check_routing: true # exclude unhealthy deployments proactively
  health_check_ignore_transient_errors: true  # ignore 429/408 noise

router_settings:
  cooldown_time: 60
  allowed_fails_policy:
    TimeoutErrorAllowedFails: 1     # cooldown on 2nd timeout
    AuthenticationErrorAllowedFails: 1  # cooldown on 2nd auth error
```

### Priority Rate Limiting (`litellm_config.yaml`)
```yaml
litellm_settings:
  saturation_threshold: 0.50   # enforce priority at 50% capacity
  default_priority: 0.1        # unkeyed requests get deprioritised
```
