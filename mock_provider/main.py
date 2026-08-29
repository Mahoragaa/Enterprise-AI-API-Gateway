"""
mock_provider/main.py — Enterprise AI API Gateway
====================================================
A lightweight FastAPI application that simulates an OpenAI-compatible LLM
provider endpoint.  LiteLLM's health checks and completion requests are
routed here during testing.

Key endpoints:
  GET  /health                  → liveness probe (used by LiteLLM & Docker)
  POST /v1/chat/completions     → fake OpenAI-compatible completion
  POST /admin/poison            → toggle the "poison pill" (forces 500 errors)
  POST /admin/cure              → restore normal operation
  GET  /admin/status            → inspect current state

The poison pill lets you simulate a hard provider outage so you can watch
LiteLLM's background health checker detect the failure and route traffic
seamlessly to the fallback deployment.
"""

import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ─── App Setup ───────────────────────────────────────────────────────────────

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "unknown")

app = FastAPI(
    title=f"Mock LLM Provider [{INSTANCE_NAME}]",
    description="Simulates an OpenAI-compatible LLM for gateway testing",
    version="1.0.0",
)

# ─── Shared mutable state ────────────────────────────────────────────────────

class ProviderState:
    def __init__(self):
        self.poisoned: bool = False            # True → return 500 on every request
        self.slow_mode: bool = False           # True → add artificial delay
        self.slow_delay_seconds: float = 5.0  # delay when slow_mode is active
        self.request_count: int = 0           # total requests served
        self.poison_activated_at: float | None = None


state = ProviderState()

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health", summary="Liveness probe")
async def health():
    """
    Simple liveness probe.  Returns 200 when healthy, 503 when poisoned.
    LiteLLM's background health checker calls this endpoint (or
    /v1/chat/completions with a minimal payload) based on model_info.mode.
    Docker also polls this for the container healthcheck.
    """
    if state.poisoned:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Provider is poisoned — simulating outage",
                "instance": INSTANCE_NAME,
            },
        )
    return {
        "status": "healthy",
        "instance": INSTANCE_NAME,
        "request_count": state.request_count,
    }


# ─── OpenAI-Compatible Chat Completions ──────────────────────────────────────

@app.post("/v1/chat/completions", summary="Fake chat completion")
async def chat_completions(request: Request):
    """
    Mimics the OpenAI /v1/chat/completions endpoint.

    - When poisoned: returns HTTP 500 (hard provider failure)
    - When slow:     sleeps for `slow_delay_seconds` before responding
    - Otherwise:     returns a deterministic fake completion immediately
    """
    state.request_count += 1

    # ── Poison pill: simulate a hard provider failure ───────────────────────
    if state.poisoned:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Internal Server Error — provider is poisoned",
                    "type": "server_error",
                    "code": "provider_outage",
                    "instance": INSTANCE_NAME,
                }
            },
        )

    # ── Slow mode: simulate timeout scenarios ───────────────────────────────
    if state.slow_mode:
        await asyncio.sleep(state.slow_delay_seconds)

    # ── Parse request body ──────────────────────────────────────────────────
    body = await request.json()
    messages = body.get("messages", [])
    last_user_msg = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "Hello",
    )
    is_streaming = body.get("stream", False)

    # ── Build an OpenAI-compatible response ─────────────────────────────────
    completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    # ── Conversational response generator ───────────────────────────────────
    msg_lower = last_user_msg.lower().strip()
    prefix = f"[{INSTANCE_NAME.upper()} MOCK]"

    if any(greet in msg_lower for greet in ["hello", "hi", "hey", "greetings", "hear me"]):
        answer = f"Hello there! 👋 I can hear you loud and clear. I am the {INSTANCE_NAME} upstream provider behind your RightLLM Gateway. All systems are operational!"
    elif any(farewell in msg_lower for farewell in ["bye", "goodbye", "see you", "cya", "farewell", "take care", "exit"]):
        answer = f"Goodbye! 👋 Signing off from the {INSTANCE_NAME} node. Gateway session logged and stored in PostgreSQL. Stay safe in cyberspace!"
    elif any(thanks in msg_lower for thanks in ["thank", "thanks", "awesome", "great", "cool", "good job"]):
        answer = f"You're very welcome! Always happy to route and process high-throughput intelligence through the gateway."
    elif any(q in msg_lower for q in ["who are you", "what are you", "what is this", "what do you do"]):
        answer = f"I am an OpenAI-compatible simulated LLM endpoint representing the {INSTANCE_NAME} cluster. Your prompts travel through the RightLLM Gateway, authenticate with Redis, and route here with zero downtime."
    elif any(q in msg_lower for q in ["how are you", "status", "health"]):
        answer = f"All systems nominal! Health probes are green, Redis rate limiters are active, and I have served {state.request_count} requests on the {INSTANCE_NAME} node."
    elif any(term in msg_lower for term in ["failover", "outage", "poison"]):
        answer = f"Our failover mechanism is powered by proactive background health checks every 10 seconds. If the primary node is poisoned, LiteLLM redirects 100% of traffic to fallback within seconds!"
    elif any(term in msg_lower for term in ["priority", "rate limit", "saturation"]):
        answer = f"Dynamic priority rate limiting ensures critical teams (Priority 0) never suffer when low-priority teams (Priority 10) flood the gateway. Saturation threshold is set to 50%."
    elif "joke" in msg_lower:
        answer = "Why do LLM gateways never get lost? Because they always know how to route!"
    else:
        answer = f"Received: \"{last_user_msg}\". Processing request #{state.request_count} through the {INSTANCE_NAME} cluster. Telemetry dispatched to Prometheus and recorded in PostgreSQL."

    reply_content = f"{prefix} {answer}"

    # ── Handle streaming (used by LiteLLM Web Playground & chat UIs) ─────────
    if is_streaming:
        async def sse_stream():
            words = reply_content.split(" ")
            for i, word in enumerate(words):
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": body.get("model", "gpt-4o"),
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": word + (" " if i < len(words) - 1 else "")},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.04)

            # Final stop chunk
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": now,
                "model": body.get("model", "gpt-4o"),
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_stream(), media_type="text/event-stream")

    return JSONResponse(
        content={
            "id": completion_id,
            "object": "chat.completion",
            "created": now,
            "model": body.get("model", "gpt-4o"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(last_user_msg.split()),
                "completion_tokens": len(reply_content.split()),
                "total_tokens": len(last_user_msg.split()) + len(reply_content.split()),
            },
        }
    )


# ─── Admin / Control Plane ───────────────────────────────────────────────────

@app.post("/admin/poison", summary="Activate poison pill (simulate outage)")
async def activate_poison():
    """
    Sets the provider to 'poisoned' state.  All subsequent calls to /health
    and /v1/chat/completions will return errors.

    LiteLLM's background health checker will detect this within
    `health_check_interval` seconds (configured as 10s in litellm_config.yaml)
    and remove this deployment from the routing pool.
    """
    state.poisoned = True
    state.poison_activated_at = time.time()
    return {
        "status": "poisoned",
        "instance": INSTANCE_NAME,
        "message": (
            f"Provider [{INSTANCE_NAME}] is now returning errors. "
            "LiteLLM should detect this within ~10 seconds and route to fallback."
        ),
    }


@app.post("/admin/cure", summary="Deactivate poison pill (restore service)")
async def deactivate_poison():
    """
    Restores the provider to healthy state.  The next health check cycle will
    re-add this deployment to the routing pool (after cooldown expires).
    """
    state.poisoned = False
    downtime = (
        round(time.time() - state.poison_activated_at, 1)
        if state.poison_activated_at
        else 0
    )
    state.poison_activated_at = None
    return {
        "status": "healthy",
        "instance": INSTANCE_NAME,
        "downtime_seconds": downtime,
        "message": (
            f"Provider [{INSTANCE_NAME}] is healthy again. "
            "It will re-enter the routing pool after the cooldown expires."
        ),
    }


@app.post("/admin/slow", summary="Enable slow mode (simulate latency/timeouts)")
async def enable_slow_mode(delay_seconds: float = 5.0):
    """
    Makes every request sleep for `delay_seconds` before responding.
    Use this to trigger LiteLLM's TimeoutErrorAllowedFails policy.
    """
    state.slow_mode = True
    state.slow_delay_seconds = delay_seconds
    return {
        "status": "slow_mode_enabled",
        "instance": INSTANCE_NAME,
        "delay_seconds": delay_seconds,
    }


@app.post("/admin/fast", summary="Disable slow mode")
async def disable_slow_mode():
    state.slow_mode = False
    return {"status": "slow_mode_disabled", "instance": INSTANCE_NAME}


@app.get("/admin/status", summary="Inspect current provider state")
async def get_status():
    """
    Returns the full current state of the mock provider — useful for
    understanding what the health checker should be seeing.
    """
    return {
        "instance": INSTANCE_NAME,
        "poisoned": state.poisoned,
        "slow_mode": state.slow_mode,
        "slow_delay_seconds": state.slow_delay_seconds if state.slow_mode else None,
        "request_count": state.request_count,
        "poison_activated_at": state.poison_activated_at,
        "poison_active_for_seconds": (
            round(time.time() - state.poison_activated_at, 1)
            if state.poison_activated_at
            else None
        ),
    }
