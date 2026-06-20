# Agent Signal Light Web MVP

This is a web-first MVP inspired by the original `agent-signal-light` project.
It keeps the core event model and browser mirror, but removes the hardware
dependency so it can be built and tested before a dev board arrives.

## What it includes

- `POST /hook` for Claude/Codex hook payloads
- `POST /event` for manual testing
- `GET /stream` for live updates over SSE
- `GET /api/status` for current aggregate state
- `GET /api/config` and `POST /api/config` for config persistence
- A browser dashboard with signal preview, sessions, event log, and mock buttons

## Run

```powershell
npm start
```

Then open [http://127.0.0.1:8787](http://127.0.0.1:8787).

## Connect real hooks

Install Codex workspace hooks and Claude global hooks:

```powershell
node .\install-hooks.js
```

This will:

- write `E:\Desktop\honhlvdeng\.codex\hooks.json` for this workspace
- merge hook commands into `~/.claude/settings.json`
- use `hook.cmd` to forward real hook payloads to the local web daemon

You can also start the dashboard with:

```powershell
.\start-server.cmd
```

## Files

- `server.js` - HTTP server, event ingestion, aggregation, SSE
- `static/` - browser UI
- `config.default.json` - default effects and event bindings copied from the original project
- `data/config.json` - local editable runtime config, created on first launch
