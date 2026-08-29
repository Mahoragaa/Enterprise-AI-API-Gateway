/**
 * RightLLM // Enterprise AI Gateway Control Center Logic
 */

const API_BASE = "";

// ── State Polling ─────────────────────────────────────────────────────────────

async function pollHealth() {
  try {
    const res = await fetch(`${API_BASE}/api/health`);
    if (!res.ok) return;
    const data = await res.json();

    // Update Top Telemetry
    const gwStatusEl = document.getElementById("val-gateway-status");
    if (data.gateway.status === "online") {
      gwStatusEl.textContent = `ONLINE (${data.gateway.latency_ms}ms)`;
      gwStatusEl.style.color = "var(--accent-cyan)";
    } else {
      gwStatusEl.textContent = "OFFLINE";
      gwStatusEl.style.color = "var(--accent-crimson)";
    }

    // Update Primary Node Card
    const cardPrimary = document.getElementById("card-primary");
    const badgePrimary = document.getElementById("badge-primary");
    const textPrimaryStatus = document.getElementById("text-primary-status");
    const btnPoison = document.getElementById("btn-poison");
    const btnCure = document.getElementById("btn-cure");

    document.getElementById("val-primary-lat").textContent = `${data.primary.latency_ms} ms`;
    document.getElementById("val-primary-reqs").textContent = data.primary.requests;

    if (data.primary.poisoned) {
      cardPrimary.classList.add("poisoned");
      badgePrimary.className = "node-status-badge badge-poisoned";
      textPrimaryStatus.textContent = "POISONED / 503";
      btnPoison.classList.add("hidden");
      btnCure.classList.remove("hidden");
    } else {
      cardPrimary.classList.remove("poisoned");
      badgePrimary.className = "node-status-badge";
      textPrimaryStatus.textContent = "ONLINE";
      btnPoison.classList.remove("hidden");
      btnCure.classList.add("hidden");
    }

    // Update Fallback Node Card
    document.getElementById("val-fallback-lat").textContent = `${data.fallback.latency_ms} ms`;
    document.getElementById("val-fallback-reqs").textContent = data.fallback.requests;
    const badgeFallback = document.getElementById("badge-fallback");
    const textFallbackStatus = document.getElementById("text-fallback-status");

    if (data.primary.poisoned) {
      badgeFallback.className = "node-status-badge";
      textFallbackStatus.textContent = "ACTIVE (RECEIVING TRAFFIC)";
    } else {
      badgeFallback.className = "node-status-badge badge-standby";
      textFallbackStatus.textContent = "STANDBY / READY";
    }

  } catch (err) {
    console.error("Health poll error:", err);
  }
}

// ── Chaos Actions ─────────────────────────────────────────────────────────────

async function triggerPoison() {
  appendSysMsg("🚨 CHAOS INJECTION: Poisoning primary upstream provider...");
  try {
    const res = await fetch(`${API_BASE}/api/chaos/poison`, { method: "POST" });
    const data = await res.json();
    appendSysMsg(`⚠️  Primary node poisoned. Status: ${data.status}. LiteLLM will auto-failover to Fallback.`);
    pollHealth();
  } catch (err) {
    appendSysMsg(`❌ Failed to poison primary node: ${err.message}`);
  }
}

async function triggerCure() {
  appendSysMsg("🩹 HEALING: Restoring primary upstream provider...");
  try {
    const res = await fetch(`${API_BASE}/api/chaos/cure`, { method: "POST" });
    const data = await res.json();
    appendSysMsg(`✅ Primary node cured (${data.status}). It will re-enter rotation after cooldown.`);
    pollHealth();
  } catch (err) {
    appendSysMsg(`❌ Failed to cure primary node: ${err.message}`);
  }
}

// ── Neural Terminal Chat ──────────────────────────────────────────────────────

async function handleSend(e) {
  e.preventDefault();
  const inputEl = document.getElementById("chat-input");
  const prompt = inputEl.value.trim();
  if (!prompt) return;

  inputEl.value = "";
  inputEl.disabled = true;
  document.getElementById("btn-transmit").disabled = true;

  // Append User Message
  appendUserMsg(prompt);

  // Loading Indicator
  const loadId = appendLoadingMsg();

  try {
    const t0 = performance.now();
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: prompt, model: "gpt-4o" }),
    });

    removeLoadingMsg(loadId);

    if (!res.ok) {
      const errData = await res.json().catch(() => ({ detail: res.statusText }));
      appendSysMsg(`❌ Gateway Error (${res.status}): ${errData.detail || "Request failed"}`);
      return;
    }

    const data = await res.json();
    appendAssistantMsg(data);

    // Refresh cluster metrics
    pollHealth();
  } catch (err) {
    removeLoadingMsg(loadId);
    appendSysMsg(`❌ Connection Error: ${err.message}`);
  } finally {
    inputEl.disabled = false;
    document.getElementById("btn-transmit").disabled = false;
    inputEl.focus();
  }
}

function quickPrompt(text) {
  const inputEl = document.getElementById("chat-input");
  inputEl.value = text;
  inputEl.focus();
}

function appendUserMsg(text) {
  const terminal = document.getElementById("terminal-body");
  const div = document.createElement("div");
  div.className = "terminal-msg msg-user";
  div.innerHTML = `
    <div class="msg-header">
      <span class="badge-node badge-user">OPERATOR // PROMPT</span>
      <span class="time">${new Date().toLocaleTimeString()}</span>
    </div>
    <div class="msg-body">${escapeHtml(text)}</div>
  `;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

function appendAssistantMsg(data) {
  const terminal = document.getElementById("terminal-body");
  const div = document.createElement("div");
  const isFallback = data.node === "fallback";
  div.className = `terminal-msg msg-assistant ${isFallback ? "fallback-reply" : ""}`;

  const nodeBadgeClass = isFallback ? "badge-fallback-node" : "badge-primary-node";
  const nodeName = isFallback ? "FALLBACK CLUSTER [FAILOVER]" : "PRIMARY CLUSTER";

  div.innerHTML = `
    <div class="msg-header">
      <span class="badge-node ${nodeBadgeClass}">${nodeName}</span>
      <span class="time">${data.latency_ms}ms · ${data.tokens} tokens</span>
    </div>
    <div class="msg-body">${escapeHtml(data.content)}</div>
  `;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

function appendLoadingMsg() {
  const terminal = document.getElementById("terminal-body");
  const id = `loading-${Date.now()}`;
  const div = document.createElement("div");
  div.id = id;
  div.className = "terminal-msg sys-msg";
  div.innerHTML = `
    <span class="time">[ROUTING]</span>
    <span class="txt">Transmitting through RightLLM Gateway across routing pool...</span>
  `;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
  return id;
}

function removeLoadingMsg(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function appendSysMsg(text) {
  const terminal = document.getElementById("terminal-body");
  const div = document.createElement("div");
  div.className = "terminal-msg sys-msg";
  div.innerHTML = `
    <span class="time">[${new Date().toLocaleTimeString()}]</span>
    <span class="txt">${escapeHtml(text)}</span>
  `;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

function clearTerminal() {
  const terminal = document.getElementById("terminal-body");
  terminal.innerHTML = `
    <div class="terminal-msg sys-msg">
      <span class="time">[SYSTEM READY]</span>
      <span class="txt">Terminal cleared. Ready for queries.</span>
    </div>
  `;
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── Priority Traffic Simulation ───────────────────────────────────────────────

async function runPrioritySimulation() {
  const btn = document.getElementById("btn-sim-traffic");
  const txt = document.getElementById("txt-sim-btn");
  const engBar = document.getElementById("eng-bar");
  const mktBar = document.getElementById("mkt-bar");
  const engStat = document.getElementById("eng-stat");
  const mktStat = document.getElementById("mkt-stat");
  const verdictEl = document.getElementById("sim-verdict-text");

  btn.disabled = true;
  txt.textContent = "FIRING 50 CONCURRENT REQUESTS...";
  verdictEl.textContent = "Simulating heavy saturation... Evaluating Redis priority queue...";

  // Reset bars
  engBar.style.width = "0%";
  mktBar.style.width = "0%";
  engStat.textContent = "Firing...";
  mktStat.textContent = "Firing...";

  try {
    const res = await fetch(`${API_BASE}/api/simulate/priority`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // Animate Engineering results
    const engPct = parseInt(data.engineering.success_rate);
    engBar.style.width = `${engPct}%`;
    engStat.textContent = `${data.engineering.success}/25 OK (${engPct}%)`;

    // Animate Marketing results
    const mktPct = parseInt(data.marketing.success_rate);
    mktBar.style.width = `${mktPct}%`;
    mktStat.textContent = `${data.marketing.success}/25 OK (${mktPct}%) · ${data.marketing.blocked_429} THROTTLED`;

    verdictEl.innerHTML = `
      <strong style="color: var(--accent-emerald)">✓ DEMO COMPLETE:</strong> ${data.verdict}
    `;

    appendSysMsg(`📊 Priority Simulation: Engineering ${data.engineering.success_rate} OK vs Marketing ${data.marketing.success_rate} OK (${data.marketing.blocked_429} requests 429-throttled).`);
    pollHealth();
  } catch (err) {
    verdictEl.textContent = `Simulation failed: ${err.message}`;
  } finally {
    btn.disabled = false;
    txt.textContent = "FIRE SATURATION BURST (50 REQS)";
  }
}

// ── Startup ───────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  pollHealth();
  setInterval(pollHealth, 3000);
});
