const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { URL } = require("node:url");

const PORT = Number(process.env.PORT || 8787);
const HOST = "127.0.0.1";
const APP_DIR = __dirname;
const STATIC_DIR = path.join(APP_DIR, "static");
const DATA_DIR = path.join(APP_DIR, "data");
const DEFAULT_CONFIG_PATH = path.join(APP_DIR, "config.default.json");
const CONFIG_PATH = path.join(DATA_DIR, "config.json");
const MANUAL_SID = "__manual__";
const SESSION_TTL_MS = 3 * 60 * 1000;
const MAX_LOG_ITEMS = 60;

const LED_MODES = { off: 0, on: 1, breathe: 2 };
const MODE_NAMES = ["off", "on", "breathe"];
const AGENT_SCOPES = new Set(["all", "claude", "codex", "cursor"]);
const CURSOR_EVENT_MAP = {
  sessionstart: "SessionStart",
  sessionend: "SessionEnd",
  beforesubmitprompt: "UserPromptSubmit",
  userpromptsubmit: "UserPromptSubmit",
  pretooluse: "PreToolUse",
  posttooluse: "PostToolUse",
  posttoolusefailure: "StopFailure",
  subagentstart: "SubagentStart",
  subagentstop: "SubagentStop",
  precompact: "PreCompact",
  postcompact: "PostCompact",
  stop: "Stop",
  stopfailure: "StopFailure",
  permissionrequest: "PermissionRequest",
  notification: "Notification",
};
const CODEX_ONLY_EVENTS = new Set(["PermissionRequest", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop"]);
const CLAUDE_ONLY_EVENTS = new Set(["Elicitation", "StopFailure"]);
const DEVICE_STATUS_PRIORITY = {
  error: 70,
  wait_confirm: 60,
  success: 50,
  busy: 40,
  ai: 30,
  thinking: 20,
  idle: 10,
  off: 0
};
const CLAUDE_EVENT_TO_STATUS = {
  SessionStart: "idle",
  UserPromptSubmit: "thinking",
  PreToolUse: "busy",
  PostToolUse: "ai",
  PostToolUseFailure: "error",
  PreCompact: "ai",
  SubagentStart: "ai",
  SubagentStop: "ai",
  PermissionRequest: "wait_confirm",
  Notification: "wait_confirm",
  Stop: "success",
  SessionEnd: "off"
};

function ensureDataDir() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(CONFIG_PATH)) {
    fs.copyFileSync(DEFAULT_CONFIG_PATH, CONFIG_PATH);
  }
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function baseEventKey(event) {
  if (typeof event !== "string") return "";
  if (event.startsWith("claude/")) return event.slice("claude/".length);
  if (event.startsWith("codex/")) return event.slice("codex/".length);
  return event;
}

function parseBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 1024 * 1024) {
        reject(new Error("body_too_large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

class ConfigStore {
  constructor() {
    this.data = readJson(CONFIG_PATH);
    this.validate(this.data);
  }

  reload() {
    this.data = readJson(CONFIG_PATH);
    this.validate(this.data);
  }

  save(nextData) {
    this.validate(nextData);
    writeJson(CONFIG_PATH, nextData);
    this.data = nextData;
    return this.data;
  }

  validate(config) {
    if (!config || typeof config !== "object") throw new Error("config must be an object");
    if (!Array.isArray(config.effects)) throw new Error("config.effects must be an array");
    if (!config.event_bindings || typeof config.event_bindings !== "object") throw new Error("config.event_bindings must be an object");
    if (!Array.isArray(config.event_priority)) throw new Error("config.event_priority must be an array");

    const effectIds = new Set();
    for (const effect of config.effects) {
      if (!effect || typeof effect.id !== "string" || !effect.id) throw new Error("every effect needs a non-empty id");
      if (effectIds.has(effect.id)) throw new Error(`duplicate effect id: ${effect.id}`);
      effectIds.add(effect.id);
      if (!Array.isArray(effect.frames) || effect.frames.length === 0) throw new Error(`effect ${effect.id} must have at least one frame`);
      for (const frame of effect.frames) {
        if (!Array.isArray(frame.leds) || frame.leds.length !== 3) throw new Error(`effect ${effect.id} has invalid frame leds`);
        for (const led of frame.leds) {
          if (!(led in LED_MODES)) throw new Error(`effect ${effect.id} uses unknown LED mode: ${led}`);
        }
        if (frame.ms !== null && (!Number.isInteger(frame.ms) || frame.ms < 10 || frame.ms > 60000)) {
          throw new Error(`effect ${effect.id} frame duration must be null or 10..60000`);
        }
      }
    }

    for (const [event, effectId] of Object.entries(config.event_bindings)) {
      if (!effectIds.has(effectId)) throw new Error(`event ${event} refers to unknown effect ${effectId}`);
    }

    for (const event of config.event_priority) {
      if (!config.event_bindings[event]) throw new Error(`event priority includes unbound event ${event}`);
    }
  }

  effectForEvent(event) {
    return this.data.event_bindings[event] || null;
  }

  getEffect(effectId) {
    return this.data.effects.find((effect) => effect.id === effectId) || null;
  }

  priorityIndex(event, agent) {
    const agentPriority = this.data.agent_priority && this.data.agent_priority[agent];
    const base = baseEventKey(event);
    if (Array.isArray(agentPriority)) {
      const idx = agentPriority.findIndex((item) => baseEventKey(item) === base);
      if (idx >= 0) return idx;
    }
    const exactIdx = this.data.event_priority.indexOf(event);
    if (exactIdx >= 0) return exactIdx;
    const baseIdx = this.data.event_priority.findIndex((item) => baseEventKey(item) === base);
    return baseIdx >= 0 ? baseIdx : Number.MAX_SAFE_INTEGER;
  }
}

class SessionStore {
  constructor(configStore) {
    this.configStore = configStore;
    this.sessions = new Map();
    this.log = [];
  }

  set(sid, event, cwd, agent) {
    this.sessions.set(sid, {
      sid,
      event,
      cwd: cwd || null,
      agent: agent || "unknown",
      lastSeen: Date.now()
    });
  }

  remove(sid) {
    return this.sessions.delete(sid);
  }

  addLog(kind, detail) {
    this.log.unshift({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      at: new Date().toISOString(),
      kind,
      detail
    });
    this.log = this.log.slice(0, MAX_LOG_ITEMS);
  }

  sweep() {
    const cutoff = Date.now() - SESSION_TTL_MS;
    let changed = false;
    for (const [sid, entry] of this.sessions.entries()) {
      if (entry.lastSeen < cutoff) {
        this.sessions.delete(sid);
        changed = true;
      }
    }
    return changed;
  }

  snapshot() {
    this.sweep();
    return [...this.sessions.values()]
      .sort((a, b) => b.lastSeen - a.lastSeen)
      .map((entry) => ({
        sid: entry.sid,
        agent: entry.agent,
        event: entry.event,
        effect_id: this.configStore.effectForEvent(entry.event) || "off",
        device_status: deriveDeviceStatus(entry.event, this.configStore.effectForEvent(entry.event) || "off", entry.agent),
        cwd: entry.cwd,
        age_s: Math.floor((Date.now() - entry.lastSeen) / 1000)
      }));
  }

  winner(agentFilter = "all") {
    const visible = this.snapshot().filter((entry) => agentFilter === "all" || entry.agent === agentFilter);
    if (visible.length === 0) return null;
    return visible.reduce((best, current) => {
      if (!best) return current;
      const bestPriority = this.configStore.priorityIndex(best.event, best.agent);
      const currentPriority = this.configStore.priorityIndex(current.event, current.agent);
      if (currentPriority < bestPriority) return current;
      if (currentPriority === bestPriority && current.age_s < best.age_s) return current;
      return best;
    }, null);
  }

  aggregate(agentFilter = "all") {
    const winner = this.winner(agentFilter);
    if (!winner) {
      return {
        effect_id: "off",
        effect_name: "Off",
        leds: ["off", "off", "off"],
        agent: "none",
        winner_event: "off"
      };
    }
    const effectId = this.configStore.effectForEvent(winner.event) || "off";
    const effect = this.configStore.getEffect(effectId);
    const firstFrame = effect?.frames?.[0] || { leds: ["off", "off", "off"] };
    return {
      effect_id: effectId,
      effect_name: effect?.name || effectId,
      leds: firstFrame.leds,
      agent: winner.agent,
      winner_event: winner.event
    };
  }
}

class SseHub {
  constructor() {
    this.clients = new Set();
  }

  add(res) {
    this.clients.add(res);
  }

  remove(res) {
    this.clients.delete(res);
  }

  send(payload) {
    const data = `data: ${JSON.stringify(payload)}\n\n`;
    for (const res of this.clients) {
      res.write(data);
    }
  }
}

ensureDataDir();
const configStore = new ConfigStore();
const sessionStore = new SessionStore(configStore);
const sseHub = new SseHub();
let agentFilter = "all";
let selectedSessionId = "";

function detectAgent(data, event, urlObj) {
  const queryAgent = (urlObj.searchParams.get("agent") || "").toLowerCase();
  const candidates = [
    queryAgent,
    data.agent_signal_source,
    data.agent,
    data.client,
    data.app,
    data.source_agent
  ];
  for (const raw of candidates) {
    const text = String(raw || "").toLowerCase();
    if (text.includes("codex")) return "codex";
    if (text.includes("claude")) return "claude";
    if (text.includes("cursor")) return "cursor";
  }
  if (CODEX_ONLY_EVENTS.has(event)) return "codex";
  if (CLAUDE_ONLY_EVENTS.has(event)) return "claude";
  return "unknown";
}

function hookEventName(data) {
  const candidates = [
    data.hook_event_name,
    data.event,
    data.event_name,
    data.hook,
    data.type,
    data.hookEventName
  ];
  for (const value of candidates) {
    const text = String(value || "").trim();
    if (text) return text;
  }
  return "";
}

function normalizeHookEvent(event) {
  const text = String(event || "").trim();
  if (!text) return "";
  const mapped = CURSOR_EVENT_MAP[text.toLowerCase()];
  return mapped || text;
}

function hookSessionId(data, agent) {
  const candidates = [
    data.session_id,
    data.sessionId,
    data.sid,
    data.conversation_id,
    data.conversationId,
    data.chat_id
  ];
  for (const value of candidates) {
    const text = String(value || "").trim();
    if (text) return text;
  }
  const cwd = String(data.cwd || data.workspace || "").trim();
  if (cwd) return `${agent}:cwd:${cwd}`;
  return "";
}

function deriveDeviceStatus(event, effectId, agent) {
  const eventText = String(event || "").trim();
  const effectText = String(effectId || "").trim();
  const normalized = `${eventText} ${effectText}`.toLowerCase();

  if (agent === "claude" && CLAUDE_EVENT_TO_STATUS[eventText]) {
    return CLAUDE_EVENT_TO_STATUS[eventText];
  }

  if (eventText === "StopFailure" || effectText === "error_red" || normalized.includes("posttoolusefailure")) {
    return "error";
  }
  if (
    eventText === "PermissionRequest" ||
    eventText === "Notification" ||
    eventText === "Elicitation" ||
    effectText === "wait_user" ||
    normalized.includes("wait_confirm") ||
    normalized.includes("waiting") ||
    normalized.includes("confirm")
  ) {
    return "wait_confirm";
  }
  if (eventText === "Stop" || effectText === "success") {
    return "success";
  }
  if (eventText === "PreToolUse" || eventText === "PostToolUse" || eventText === "busy") {
    return "busy";
  }
  if (
    eventText === "SubagentStart" ||
    eventText === "SubagentStop" ||
    eventText === "PreCompact" ||
    eventText === "PostCompact" ||
    eventText === "ai"
  ) {
    return "ai";
  }
  if (
    eventText === "SessionStart" ||
    effectText === "idle_green"
  ) {
    return "idle";
  }
  if (
    eventText === "UserPromptSubmit" ||
    eventText === "thinking" ||
    effectText === "working_yellow"
  ) {
    return "thinking";
  }
  return effectText === "off" || eventText === "SessionEnd" ? "off" : "off";
}

function effectIdForDeviceStatus(deviceStatus) {
  switch (deviceStatus) {
    case "idle":
      return "idle_green";
    case "thinking":
    case "ai":
    case "busy":
      return "working_yellow";
    case "wait_confirm":
      return "wait_user";
    case "error":
      return "error_red";
    case "success":
      return "success";
    default:
      return "off";
  }
}

function shouldTrackEvent(event, agent) {
  const deviceStatus = deriveDeviceStatus(event, "", agent);
  return deviceStatus !== "off" || event === "SessionEnd" || event === "Stop" || event === "SessionStart";
}

function bindingKeyFor(data, event, agent) {
  const tool = String(data.tool_name || "").trim();
  const matcherValue =
    event === "PreToolUse" || event === "PostToolUse" || event === "PermissionRequest"
      ? tool
      : "";

  const candidates = [];
  if (matcherValue) {
    if (agent === "claude" || agent === "codex") candidates.push(`${agent}/${event}:${matcherValue}`);
    candidates.push(`${event}:${matcherValue}`);
  }
  if (agent === "claude" || agent === "codex") candidates.push(`${agent}/${event}`);
  candidates.push(event);

  return candidates.find((key) => configStore.effectForEvent(key)) || null;
}

function statusPayload() {
  const sessions = sessionStore.snapshot();
  const visibleSessions = sessions.filter((session) => agentFilter === "all" || session.agent === agentFilter);
  const selectedSession =
    selectedSessionId
      ? visibleSessions.find((session) => session.sid === selectedSessionId) || null
      : null;
  const aggregate = selectedSession
    ? {
        effect_id: effectIdForDeviceStatus(selectedSession.device_status),
        effect_name: effectIdForDeviceStatus(selectedSession.device_status),
        leds: (configStore.getEffect(effectIdForDeviceStatus(selectedSession.device_status))?.frames?.[0]?.leds) || ["off", "off", "off"],
        agent: selectedSession.agent,
        winner_event: selectedSession.event,
        device_status: selectedSession.device_status
      }
    : (() => {
        const winner = visibleSessions.reduce((best, current) => {
          if (!best) return current;
          const bestPriority = DEVICE_STATUS_PRIORITY[best.device_status] ?? -1;
          const currentPriority = DEVICE_STATUS_PRIORITY[current.device_status] ?? -1;
          if (currentPriority > bestPriority) return current;
          if (currentPriority === bestPriority && current.age_s < best.age_s) return current;
          return best;
        }, null);

        if (!winner) {
          return {
            effect_id: "off",
            effect_name: "Off",
            leds: ["off", "off", "off"],
            agent: "none",
            winner_event: "off",
            device_status: "off"
          };
        }

        const effectId = effectIdForDeviceStatus(winner.device_status);
        return {
          effect_id: effectId,
          effect_name: effectId,
          leds: (configStore.getEffect(effectId)?.frames?.[0]?.leds) || ["off", "off", "off"],
          agent: winner.agent,
          winner_event: winner.event,
          device_status: winner.device_status
        };
      })();
  const agentCounts = sessions.reduce((acc, session) => {
    if (session.agent === "codex" || session.agent === "claude" || session.agent === "cursor") {
      acc[session.agent] += 1;
    }
    return acc;
  }, { codex: 0, claude: 0, cursor: 0 });
  return {
    ok: true,
    agent_filter: agentFilter,
    selected_session_id: selectedSessionId || "",
    controlling_session_id: selectedSession ? selectedSession.sid : "",
    selected_session_missing: Boolean(selectedSessionId) && !selectedSession,
    ...aggregate,
    led_codes: aggregate.leds.map((mode) => LED_MODES[mode]),
    display_state: aggregate.device_status === "wait_confirm" ? "waiting" : aggregate.device_status,
    sessions,
    visible_session_count: visibleSessions.length,
    agent_counts: agentCounts,
    log: sessionStore.log,
    config: configStore.data
  };
}

function broadcast() {
  sseHub.send(statusPayload());
}

setInterval(() => {
  const changed = sessionStore.sweep();
  if (changed) {
    if (selectedSessionId && !sessionStore.sessions.has(selectedSessionId)) {
      selectedSessionId = "";
    }
    broadcast();
  }
}, 1000);

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  res.end(JSON.stringify(payload));
}

function sendText(res, statusCode, body, contentType) {
  res.writeHead(statusCode, {
    "Content-Type": contentType,
    "Cache-Control": "no-store"
  });
  res.end(body);
}

function serveFile(res, filePath, contentType) {
  try {
    const body = fs.readFileSync(filePath);
    sendText(res, 200, body, contentType);
  } catch {
    sendText(res, 404, "Not Found", "text/plain; charset=utf-8");
  }
}

async function handleHook(req, res, urlObj) {
  const raw = await parseBody(req);
  let data;
  try {
    data = JSON.parse(raw.toString("utf8"));
  } catch {
    sendJson(res, 400, { ok: false, error: "bad json" });
    return;
  }

  const sid = String(data.session_id || "").trim();
  const event = normalizeHookEvent(hookEventName(data));
  const cwd = data.cwd ? String(data.cwd) : null;
  const agent = detectAgent(data, event, urlObj);
  const resolvedSid = hookSessionId(data, agent);
  if (!resolvedSid || !event) {
    sendJson(res, 400, { ok: false, error: "missing session id or event name" });
    return;
  }

  if (event === "SessionEnd") {
    sessionStore.remove(resolvedSid);
    sessionStore.addLog("session-end", `${agent}:${resolvedSid.slice(0, 8)}`);
    broadcast();
    sendJson(res, 200, { ok: true });
    return;
  }

  if (!shouldTrackEvent(event, agent)) {
    sendJson(res, 200, { ok: true, ignored: true });
    return;
  }

  sessionStore.set(resolvedSid, event, cwd, agent);
  sessionStore.addLog("hook", `${agent} ${event} ${resolvedSid.slice(0, 8)}`);
  broadcast();
  sendJson(res, 200, { ok: true, event, agent });
}

async function handleManualEvent(req, res) {
  const raw = (await parseBody(req)).toString("utf8").trim().toUpperCase();
  const map = {
    G: "SessionStart",
    Y: "UserPromptSubmit",
    W: "PermissionRequest",
    R: "StopFailure",
    O: "SessionEnd"
  };
  const event = map[raw];
  if (!event) {
    sendJson(res, 400, { ok: false, error: "use G/Y/W/R/O" });
    return;
  }
  if (event === "SessionEnd") {
    sessionStore.remove(MANUAL_SID);
    sessionStore.addLog("manual", "manual off");
  } else {
    sessionStore.set(MANUAL_SID, event, "(manual)", "manual");
    sessionStore.addLog("manual", `manual ${event}`);
  }
  broadcast();
  sendJson(res, 200, { ok: true, event });
}

async function handleConfigSave(req, res) {
  const raw = await parseBody(req);
  let nextConfig;
  try {
    nextConfig = JSON.parse(raw.toString("utf8"));
    configStore.save(nextConfig);
  } catch (error) {
    sendJson(res, 400, { ok: false, error: error.message || "invalid config" });
    return;
  }
  sessionStore.addLog("config", "config updated");
  broadcast();
  sendJson(res, 200, { ok: true, config: configStore.data });
}

async function handleAgentFilter(req, res) {
  const raw = await parseBody(req);
  let nextScope = "";
  try {
    const data = JSON.parse(raw.toString("utf8") || "{}");
    nextScope = String(data.scope || data.agent_filter || "").toLowerCase();
  } catch {
    sendJson(res, 400, { ok: false, error: "bad json" });
    return;
  }
  if (!AGENT_SCOPES.has(nextScope)) {
    sendJson(res, 400, { ok: false, error: "scope must be all, claude, codex, or cursor" });
    return;
  }
  agentFilter = nextScope;
  broadcast();
  sendJson(res, 200, { ok: true, agent_filter: agentFilter });
}

async function handleSessionSelect(req, res) {
  const raw = await parseBody(req);
  let nextSid = "";
  try {
    const data = JSON.parse(raw.toString("utf8") || "{}");
    nextSid = String(data.sid || data.session_id || "").trim();
  } catch {
    sendJson(res, 400, { ok: false, error: "bad json" });
    return;
  }

  if (nextSid) {
    const exists = sessionStore.snapshot().some((session) => session.sid === nextSid);
    if (!exists) {
      sendJson(res, 404, { ok: false, error: "session not found" });
      return;
    }
  }

  selectedSessionId = nextSid;
  broadcast();
  sendJson(res, 200, {
    ok: true,
    selected_session_id: selectedSessionId
  });
}

const server = http.createServer(async (req, res) => {
  const urlObj = new URL(req.url, `http://${req.headers.host || `${HOST}:${PORT}`}`);

  try {
    if (req.method === "GET" && urlObj.pathname === "/") {
      serveFile(res, path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8");
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/app.js") {
      serveFile(res, path.join(STATIC_DIR, "app.js"), "application/javascript; charset=utf-8");
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/ble-client.js") {
      serveFile(res, path.join(STATIC_DIR, "ble-client.js"), "application/javascript; charset=utf-8");
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/device-transport.js") {
      serveFile(res, path.join(STATIC_DIR, "device-transport.js"), "application/javascript; charset=utf-8");
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/styles.css") {
      serveFile(res, path.join(STATIC_DIR, "styles.css"), "text/css; charset=utf-8");
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/api/status") {
      sendJson(res, 200, statusPayload());
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/api/config") {
      sendJson(res, 200, { ok: true, config: configStore.data });
      return;
    }
    if (req.method === "GET" && urlObj.pathname === "/stream") {
      res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive"
      });
      res.write(`data: ${JSON.stringify(statusPayload())}\n\n`);
      sseHub.add(res);
      req.on("close", () => sseHub.remove(res));
      return;
    }
    if (req.method === "POST" && urlObj.pathname === "/hook") {
      await handleHook(req, res, urlObj);
      return;
    }
    if (req.method === "POST" && urlObj.pathname === "/event") {
      await handleManualEvent(req, res);
      return;
    }
    if (req.method === "POST" && urlObj.pathname === "/api/config") {
      await handleConfigSave(req, res);
      return;
    }
    if (req.method === "POST" && urlObj.pathname === "/api/agent-filter") {
      await handleAgentFilter(req, res);
      return;
    }
    if (req.method === "POST" && urlObj.pathname === "/api/session-select") {
      await handleSessionSelect(req, res);
      return;
    }

    sendText(res, 404, "Not Found", "text/plain; charset=utf-8");
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error.message || "server error" });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`Agent Signal Light Web MVP -> http://${HOST}:${PORT}`);
});
