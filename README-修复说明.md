# AI 状态灯 — 修复说明

本文档记录近期对 Agent Signal Light 项目的修复内容，便于后续维护、回归测试和客户交付说明。

---

## 1. 任务完成后未回到 idle（主要修复）

### 现象

AI 任务成功结束（`Stop` 事件 / 绿色常亮 `success`）后，灯应停留约 5 秒，然后自动回到 **idle**（绿色呼吸）。实际表现为一直停在 success，或很久才回到 idle。

### 根因

问题出在 **上位机与固件配合**，不是单一模块的 bug：

| 层级 | 问题 |
|------|------|
| **Web 服务** (`server.js`) | `Stop` 事件写入 session 后，`device_status` 长期保持 `success`，不会自动衰减为 `idle`。 |
| **串口桥** (`codex_status_bridge.py`) | 每 0.5s 轮询 API，每 2s 重发当前命令。API 一直返回 `success` 时，桥接会持续向 ESP32 发送 `success`。 |
| **固件** (`main.ino`) | 每次收到 `success` 都会调用 `enterState(STATE_SUCCESS)` 并重置 5 秒计时器。桥接每 2s 重发一次，计时器被反复清零，可能永远到不了 idle。 |
| **Web UI** (`app.js`) | 客户端有 5 秒后显示 idle 的逻辑，但只改前端展示，**不更新**服务端 session，桥接看不到 idle。 |

固件本身在**没有重复 success 命令**时，5 秒后是会正确切 idle 的。

### 修复方案

#### 上位机 — `src/agent-signal-light-web/server.js`

- 新增常量 `SUCCESS_HOLD_MS = 5000`（与固件一致）。
- 在 `SessionStore.sweep()` 中：若 session 的 `event === "Stop"` 且距 `lastSeen` 已超过 5 秒，自动将 `event` 改为 `SessionStart`，对应 `device_status: idle`。
- API `/api/status` 与 SSE 推送在 success 过期后会返回 idle，桥接不再无限重发 success。

#### 固件 — `esp32_c3_traffic_light.ino` / `src/main.ino`

- 已在 `STATE_SUCCESS` 时，**忽略重复的 `success` 命令**，避免计时器被桥接重发重置。
- success 自动切 idle 时增加串口日志：`State changed to: idle`，便于调试。

### 验证方法

**1. 串口直测（先停桥接）**

```bash
pkill -f codex_status_bridge.py
# 使用手动测试菜单或 echo 发命令
echo "success" > /dev/cu.usbmodem2101   # 按实际端口修改
# 等待 6 秒后，串口应出现：State changed to: idle
# 灯效：绿常亮 5s → 绿色呼吸
```

**2. API 衰减测试**

```bash
curl -s -X POST http://127.0.0.1:8787/hook \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test-decay","hook_event_name":"Stop","cwd":"/tmp"}'

curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool
# 立即：device_status 为 success，winner_event 为 Stop

sleep 6
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool
# 6 秒后：该 session 的 event 变为 SessionStart，device_status 为 idle
```

**3. 完整链路**

```bash
cd src && ./00-一键安装并启动.sh --start-only
# 触发一次任务完成（Stop），观察 5 秒后灯是否回到绿色呼吸
```

> **注意**：若同时存在更高优先级会话（如 `PreToolUse` → busy），全局状态会优先显示 busy，这是预期行为。只有在「无其他活跃高优先级事件」时，success 才会在 5 秒后成为 idle 并驱动硬件。

---

## 2. 灯效与命令映射（此前修复汇总）

以下状态已在固件与上位机对齐，供回归参考：

| 命令 / 状态 | 灯效 |
|-------------|------|
| `idle` | 绿灯呼吸 |
| `thinking` | 红 → 黄 → 绿 交替 |
| `busy` | 黄灯慢闪 |
| `wait_confirm` | 黄灯快闪（550ms） |
| `error` | 红灯快闪（130ms） |
| `success` | 绿灯常亮 5 秒，然后自动 idle |
| `off` | 全灭 |

GPIO 引脚（物理接线）：

- 红灯：`GPIO 21`
- 黄灯：`GPIO 10`
- 绿灯：`GPIO 20`

---

## 3. 设备 ID 识别（可选功能）

固件启动时输出：

```
READY ID=agent-signal-light-v1
```

支持串口命令 `identify` / `id` 查询设备 ID。Python 桥接与手动测试脚本通过 `serial_device.py` 按 ID 自动探测串口，避免多设备或端口变化时连错设备。

---

## 4. Web 手动测试按钮（此前修复）

- 手动点击模拟按钮时，服务端优先使用 `__manual__` session，避免被其他 Cursor/Codex 会话覆盖。
- 前端点击后进入 `test` 模式，确保手动指令生效。

---

## 5. 涉及文件

| 文件 | 变更说明 |
|------|----------|
| `src/agent-signal-light-web/server.js` | success 5 秒后 session 衰减为 idle |
| `src/esp32_c3_traffic_light/esp32_c3_traffic_light.ino` | 忽略重复 success；idle 切换日志 |
| `src/esp32_c3_traffic_light/src/main.ino` | 与上相同（PlatformIO 构建副本，需保持同步） |
| `src/codex_status_bridge.py` | 轮询 API 并下发串口命令（依赖 server 正确返回 idle） |
| `src/serial_device.py` | 按 DEVICE_ID 探测串口 |
| `src/agent-signal-light-web/static/app.js` | 前端 success 展示 5 秒后切 idle（仅 UI） |

---

## 6. 部署与重启

修改 **server.js** 后需重启 Web 服务：

```bash
pkill -f "agent-signal-light-web/server.js"
cd src && ./00-一键安装并启动.sh --start-only
```

修改 **固件** 后需重新烧录：

```bash
pkill -f codex_status_bridge.py   # 释放串口
cd src/esp32_c3_traffic_light
python3 -m platformio run -t upload --upload-port /dev/cu.usbmodem2101
cd ../.. && cd src && ./00-一键安装并启动.sh --start-only
```

---

## 7. 常见问题

**Q：手动测试时灯不跟命令走？**  
A：先停止后台桥接：`pkill -f codex_status_bridge.py`，再用 `06-双击这里-手动测试灯效.cmd` 或 `agent_light_control.py` 测试。

**Q：success 一直不回到 idle？**  
A：确认 Web 服务已重启（加载新 `server.js`）、固件已烧录（忽略重复 success），且没有其他 hook 会话占用更高优先级状态。

**Q：Arduino IDE 与 PlatformIO 两份 .ino 不一致？**  
A：`esp32_c3_traffic_light.ino` 与 `src/main.ino` 应保持一致；PlatformIO 以 `src/main.ino` 为准编译。

---

## 8. 时间线常量（需保持一致）

| 位置 | 常量 | 值 |
|------|------|-----|
| 固件 | `SUCCESS_HOLD_MS` | 5000 ms |
| Web 服务 | `SUCCESS_HOLD_MS` | 5000 ms |
| 桥接 | `COMMAND_RESEND_SECONDS` | 2.0 s |
| 桥接 | 轮询间隔 `DEFAULT_INTERVAL` | 0.5 s |

若调整 success 停留时长，请同时修改固件与 `server.js` 中的 `SUCCESS_HOLD_MS`。
