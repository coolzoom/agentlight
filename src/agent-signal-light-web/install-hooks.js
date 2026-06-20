const fs = require("node:fs");
const path = require("node:path");

const workspaceRoot = path.resolve(__dirname, "..");
const workspaceCodexDir = path.join(workspaceRoot, ".codex");
const workspaceHooksPath = path.join(workspaceCodexDir, "hooks.json");
const userCodexDir = path.join(process.env.USERPROFILE || process.env.HOME || "", ".codex");
const userCodexHooksPath = path.join(userCodexDir, "hooks.json");
const claudeDir = path.join(process.env.USERPROFILE || process.env.HOME || "", ".claude");
const claudeSettingsPath = path.join(claudeDir, "settings.json");
const isWindows = process.platform === "win32";
const hookEntryFile = isWindows ? "hook.cmd" : "hook.sh";
const hookEntryAbs = path.join(__dirname, hookEntryFile);
const hookEntryRel = isWindows ? ".\\agent-signal-light-web\\hook.cmd" : "./agent-signal-light-web/hook.sh";

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function codexHookCommand() {
  return `${hookEntryRel} codex`;
}

function codexGlobalHookCommand() {
  if (isWindows) {
    return `cmd /c ""${hookEntryAbs}" codex"`;
  }
  return `sh "${hookEntryAbs}" codex`;
}

function claudeHookCommand() {
  if (isWindows) {
    return `"${hookEntryAbs}" claude`;
  }
  return `sh "${hookEntryAbs}" claude`;
}

function buildCodexHookConfig(command) {
  const hook = {
    type: "command",
    command,
    commandWindows: command,
    timeout: 5
  };
  return {
    hooks: {
      SessionStart: [{ matcher: "startup|resume|clear|compact", hooks: [hook] }],
      UserPromptSubmit: [{ hooks: [hook] }],
      PreToolUse: [{ matcher: ".*", hooks: [hook] }],
      PostToolUse: [{ matcher: ".*", hooks: [hook] }],
      PermissionRequest: [{ matcher: ".*", hooks: [hook] }],
      PreCompact: [{ matcher: "manual|auto", hooks: [hook] }],
      PostCompact: [{ matcher: "manual|auto", hooks: [hook] }],
      SubagentStart: [{ hooks: [hook] }],
      SubagentStop: [{ hooks: [hook] }],
      Stop: [{ hooks: [hook] }],
      StopFailure: [{ hooks: [hook] }]
    }
  };
}

function codexHooksConfig() {
  return buildCodexHookConfig(codexHookCommand());
}

function codexGlobalHooksConfig() {
  return buildCodexHookConfig(codexGlobalHookCommand());
}

function buildClaudeHooks() {
  const command = claudeHookCommand();
  const commandHook = { type: "command", command };
  const group = () => ({ matcher: "", hooks: [commandHook] });
  return {
    SessionStart: [group()],
    SessionEnd: [group()],
    UserPromptSubmit: [group()],
    PreToolUse: [group()],
    PostToolUse: [group()],
    PostToolUseFailure: [group()],
    PreCompact: [group()],
    SubagentStart: [group()],
    SubagentStop: [group()],
    PermissionRequest: [group()],
    Notification: [group()],
    Stop: [group()],
  };
}

function installCodexHooks() {
  ensureDir(workspaceCodexDir);
  writeJson(workspaceHooksPath, codexHooksConfig());
  console.log(`wrote Codex hooks -> ${workspaceHooksPath}`);
}

function mergeCodexHooks(filePath, oursFactory, label) {
  ensureDir(path.dirname(filePath));
  const current = readJson(filePath, {});
  const next = typeof current === "object" && current !== null ? current : {};
  const hooksRoot = typeof next.hooks === "object" && next.hooks !== null ? next.hooks : {};
  const ours = oursFactory().hooks;

  for (const [eventName, groups] of Object.entries(ours)) {
    const existing = Array.isArray(hooksRoot[eventName]) ? hooksRoot[eventName] : [];
    const kept = existing.filter((group) => {
      try {
        const first = group.hooks?.[0]?.command || "";
        const command = String(first);
        const isCurrentSignalLightHook =
          command.includes("agent-signal-light-web") && (command.includes("hook.cmd") || command.includes("hook.sh"));
        const isLegacySignalLightHook =
          command.includes("codex_light_hook.py") ||
          command.includes("codex_light_serial.py") ||
          command.includes("sketch_may27a");
        return !isCurrentSignalLightHook && !isLegacySignalLightHook;
      } catch {
        return true;
      }
    });
    hooksRoot[eventName] = [...kept, ...groups];
  }

  next.hooks = hooksRoot;
  writeJson(filePath, next);
  console.log(`${label} -> ${filePath}`);
}

function installUserCodexHooks() {
  mergeCodexHooks(userCodexHooksPath, codexGlobalHooksConfig, "merged user Codex hooks");
}

function installClaudeHooks() {
  ensureDir(claudeDir);
  const current = readJson(claudeSettingsPath, {});
  const next = typeof current === "object" && current !== null ? current : {};
  const hooksRoot = typeof next.hooks === "object" && next.hooks !== null ? next.hooks : {};
  const ours = buildClaudeHooks();

  for (const [eventName, groups] of Object.entries(ours)) {
    const existing = Array.isArray(hooksRoot[eventName]) ? hooksRoot[eventName] : [];
    const kept = existing.filter((group) => {
      try {
        const first = group.hooks?.[0]?.command || "";
        const command = String(first);
        const isCurrentHook =
          command.includes("agent-signal-light-web") &&
          (command.includes("hook.cmd") || command.includes("hook.sh"));
        return !isCurrentHook;
      } catch {
        return true;
      }
    });
    hooksRoot[eventName] = [...kept, ...groups];
  }

  next.hooks = hooksRoot;
  writeJson(claudeSettingsPath, next);
  console.log(`merged Claude hooks -> ${claudeSettingsPath}`);
}

installCodexHooks();
installUserCodexHooks();
installClaudeHooks();
