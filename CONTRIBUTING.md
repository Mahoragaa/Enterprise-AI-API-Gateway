# Contributing to Enterprise AI API Gateway

Thank you for your interest in contributing! This guide covers local development setup and how to extend the project.

---

## Local Development (Without Docker)

You can run the mock provider and test scripts directly on your machine for faster iteration.

### Prerequisites

```bash
pip install -r requirements.txt
```

You still need Redis running. The quickest way:

```bash
docker run -d --name dev-redis -p 6379:6379 redis:7-alpine
```

### Run the mock providers locally

```bash
# Terminal 1 — primary mock (port 9000)
INSTANCE_NAME=primary uvicorn mock_provider.main:app --port 9000 --reload

# Terminal 2 — fallback mock (port 9001)
INSTANCE_NAME=fallback uvicorn mock_provider.main:app --port 9001 --reload
```

### Run LiteLLM locally (if installed)

```bash
pip install litellm[proxy]
LITELLM_MASTER_KEY=sk-master-key-1234 \
REDIS_HOST=localhost REDIS_PORT=6379 \
OPENAI_API_KEY=sk-fake \
litellm --config litellm_config.yaml --port 4000 --detailed_debug
```

> **Note:** The `litellm_config.yaml` uses Docker service names (`mock-api-primary`, `mock-api-fallback`).
> For local dev, you'll need to temporarily change `api_base` to `http://localhost:9000/v1` and `http://localhost:9001/v1`.

---

## Adding New Mock Failure Modes

The mock provider is in `mock_provider/main.py`. All state lives in the `ProviderState` class at the top.

### Example: Add a "partial failure" mode (returns 500 on 50% of requests)

1. Add a flag to `ProviderState`:
   ```python
   self.flaky_mode: bool = False
   self.flaky_rate: float = 0.5
   ```

2. Add the check in `chat_completions()`:
   ```python
   if state.flaky_mode and random.random() < state.flaky_rate:
       raise HTTPException(status_code=500, detail={"error": "flaky failure"})
   ```

3. Add admin endpoints:
   ```python
   @app.post("/admin/flaky")
   async def enable_flaky(rate: float = 0.5):
       state.flaky_mode = True
       state.flaky_rate = rate
       return {"status": "flaky_enabled", "rate": rate}

   @app.post("/admin/stable")
   async def disable_flaky():
       state.flaky_mode = False
       return {"status": "stable"}
   ```

---

## Code Style

This project uses **ruff** for linting and formatting:

```bash
# Check
ruff check mock_provider/main.py setup_teams_and_test.py

# Auto-fix
ruff check --fix mock_provider/main.py setup_teams_and_test.py

# Format
ruff format mock_provider/main.py setup_teams_and_test.py
```

The CI pipeline enforces this on every push.

---

## Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(component): short description
fix(component): what was broken
chore: non-code changes (deps, docs)
test: adding or updating tests
```

Examples:
- `feat(mock): add flaky mode endpoint`
- `fix(config): correct health_check_interval typo`
- `chore: bump litellm image version`

---

## Pull Request Checklist

- [ ] Code passes `ruff check` and `ruff format --check`
- [ ] New features have corresponding test coverage in `setup_teams_and_test.py`
- [ ] `README.md` updated if user-facing behaviour changes
- [ ] Docker image still builds: `docker compose build`
