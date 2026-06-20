#!/usr/bin/env python3
import json
import os
import platform
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_DIR.parent
WORKSPACE_CODEX_DIR = WORKSPACE_ROOT / ".codex"
WORKSPACE_HOOKS_PATH = WORKSPACE_CODEX_DIR / "hooks.json"
HOME = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or "")
USER_CODEX_DIR = HOME / ".codex"
USER_CODEX_HOOKS_PATH = USER_CODEX_DIR / "hooks.json"
CLAUDE_DIR = HOME / ".claude"
CLAUDE_SETTINGS_PATH = CLAUDE_DIR / "settings.json"
IS_WINDOWS = platform.system().lower() == "windows"
HOOK_ENTRY_FILE = "hook.cmd" if IS_WINDOWS else "hook.sh"
HOOK_ENTRY_ABS = APP_DIR / HOOK_ENTRY_FILE
HOOK_ENTRY_REL = f".\\agent-signal-light-web\\{HOOK_ENTRY_FILE}" if IS_WINDOWS else "./agent-signal-light-web/hook.sh"


def ensure_dir(dir_path):
    dir_path.mkdir(parents=True, exist_ok=True)


def read_json(file_path, fallback):
    try:
        with open(file_path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(file_path, value):
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def codex_hook_command():
    return f"{HOOK_ENTRY_REL} codex"


def codex_global_hook_command():
    if IS_WINDOWS:
        return f'cmd /c ""{HOOK_ENTRY_ABS}" codex"'
    return f'sh "{HOOK_ENTRY_ABS}" codex'


def claude_hook_command():
    if IS_WINDOWS:
        return f'"{HOOK_ENTRY_ABS}" claude'
    return f'sh "{HOOK_ENTRY_ABS}" claude'


def cursor_hook_command():
    return f"{HOOK_ENTRY_REL} cursor"


def cursor_global_hook_command():
    if IS_WINDOWS:
        return f'cmd /c ""{HOOK_ENTRY_ABS}" cursor"'
    return f'sh "{HOOK_ENTRY_ABS}" cursor'


def build_cursor_hooks_config(command):
    entry = {"command": command, "timeout": 5}
    return {
        "version": 1,
        "hooks": {
            "sessionStart": [entry],
            "sessionEnd": [entry],
            "beforeSubmitPrompt": [entry],
            "preToolUse": [entry],
            "postToolUse": [entry],
            "postToolUseFailure": [entry],
            "subagentStart": [entry],
            "subagentStop": [entry],
            "preCompact": [entry],
            "stop": [entry],
        },
    }


def build_codex_hook_config(command):
    hook = {
        "type": "command",
        "command": command,
        "commandWindows": command,
        "timeout": 5,
    }
    return {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear|compact", "hooks": [hook]}],
            "UserPromptSubmit": [{"hooks": [hook]}],
            "PreToolUse": [{"matcher": ".*", "hooks": [hook]}],
            "PostToolUse": [{"matcher": ".*", "hooks": [hook]}],
            "PermissionRequest": [{"matcher": ".*", "hooks": [hook]}],
            "PreCompact": [{"matcher": "manual|auto", "hooks": [hook]}],
            "PostCompact": [{"matcher": "manual|auto", "hooks": [hook]}],
            "SubagentStart": [{"hooks": [hook]}],
            "SubagentStop": [{"hooks": [hook]}],
            "Stop": [{"hooks": [hook]}],
            "StopFailure": [{"hooks": [hook]}],
        }
    }


def codex_hooks_config():
    return build_codex_hook_config(codex_hook_command())


def codex_global_hooks_config():
    return build_codex_hook_config(codex_global_hook_command())


def build_claude_hooks():
    command = claude_hook_command()
    command_hook = {"type": "command", "command": command}

    def group():
        return {"matcher": "", "hooks": [command_hook]}

    return {
        "SessionStart": [group()],
        "SessionEnd": [group()],
        "UserPromptSubmit": [group()],
        "PreToolUse": [group()],
        "PostToolUse": [group()],
        "PostToolUseFailure": [group()],
        "PreCompact": [group()],
        "SubagentStart": [group()],
        "SubagentStop": [group()],
        "PermissionRequest": [group()],
        "Notification": [group()],
        "Stop": [group()],
    }


def is_signal_light_hook_command(command):
    text = str(command)
    return "agent-signal-light-web" in text and ("hook.cmd" in text or "hook.sh" in text)


def is_legacy_signal_light_hook(command):
    text = str(command)
    return (
        "codex_light_hook.py" in text
        or "codex_light_serial.py" in text
        or "sketch_may27a" in text
    )


def install_codex_hooks():
    ensure_dir(WORKSPACE_CODEX_DIR)
    write_json(WORKSPACE_HOOKS_PATH, codex_hooks_config())
    print(f"wrote Codex hooks -> {WORKSPACE_HOOKS_PATH}")


def merge_codex_hooks(file_path, ours_factory, label):
    ensure_dir(file_path.parent)
    current = read_json(file_path, {})
    next_data = current if isinstance(current, dict) else {}
    hooks_root = next_data.get("hooks") if isinstance(next_data.get("hooks"), dict) else {}
    ours = ours_factory()["hooks"]

    for event_name, groups in ours.items():
        existing = hooks_root.get(event_name) if isinstance(hooks_root.get(event_name), list) else []
        kept = []
        for group in existing:
            try:
                first = (group.get("hooks") or [{}])[0].get("command", "")
                command = str(first)
                if is_signal_light_hook_command(command) or is_legacy_signal_light_hook(command):
                    continue
                kept.append(group)
            except (AttributeError, IndexError, TypeError):
                kept.append(group)
        hooks_root[event_name] = kept + groups

    next_data["hooks"] = hooks_root
    write_json(file_path, next_data)
    print(f"{label} -> {file_path}")


def install_user_codex_hooks():
    merge_codex_hooks(USER_CODEX_HOOKS_PATH, codex_global_hooks_config, "merged user Codex hooks")


def install_claude_hooks():
    ensure_dir(CLAUDE_DIR)
    current = read_json(CLAUDE_SETTINGS_PATH, {})
    next_data = current if isinstance(current, dict) else {}
    hooks_root = next_data.get("hooks") if isinstance(next_data.get("hooks"), dict) else {}
    ours = build_claude_hooks()

    for event_name, groups in ours.items():
        existing = hooks_root.get(event_name) if isinstance(hooks_root.get(event_name), list) else []
        kept = []
        for group in existing:
            try:
                first = (group.get("hooks") or [{}])[0].get("command", "")
                if is_signal_light_hook_command(str(first)):
                    continue
                kept.append(group)
            except (AttributeError, IndexError, TypeError):
                kept.append(group)
        hooks_root[event_name] = kept + groups

    next_data["hooks"] = hooks_root
    write_json(CLAUDE_SETTINGS_PATH, next_data)
    print(f"merged Claude hooks -> {CLAUDE_SETTINGS_PATH}")


def is_signal_light_cursor_hook(entry):
    try:
        return is_signal_light_hook_command(entry.get("command", ""))
    except AttributeError:
        return False


def merge_cursor_hooks(file_path, command_factory, label):
    ensure_dir(file_path.parent)
    current = read_json(file_path, {})
    next_data = current if isinstance(current, dict) else {}
    hooks_root = next_data.get("hooks") if isinstance(next_data.get("hooks"), dict) else {}
    ours = build_cursor_hooks_config(command_factory())["hooks"]

    for event_name, entries in ours.items():
        existing = hooks_root.get(event_name) if isinstance(hooks_root.get(event_name), list) else []
        kept = [entry for entry in existing if not is_signal_light_cursor_hook(entry)]
        hooks_root[event_name] = kept + entries

    next_data["version"] = 1
    next_data["hooks"] = hooks_root
    write_json(file_path, next_data)
    print(f"{label} -> {file_path}")


def install_cursor_hooks():
    workspace_cursor_dir = WORKSPACE_ROOT / ".cursor"
    merge_cursor_hooks(
        workspace_cursor_dir / "hooks.json",
        cursor_hook_command,
        "wrote Cursor hooks",
    )


def install_user_cursor_hooks():
    user_cursor_dir = HOME / ".cursor"
    merge_cursor_hooks(
        user_cursor_dir / "hooks.json",
        cursor_global_hook_command,
        "merged user Cursor hooks",
    )


def main():
    install_codex_hooks()
    install_user_codex_hooks()
    install_claude_hooks()
    install_cursor_hooks()
    install_user_cursor_hooks()


if __name__ == "__main__":
    main()
