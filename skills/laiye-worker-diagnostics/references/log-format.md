# 日志格式参考

## 日志文件路径与用途

不要写死某台机器的用户名。Agent 日志优先使用 `HERMES_HOME`；未设置时 Windows 默认 `%LOCALAPPDATA%\hermes`，macOS/Linux 默认 `~/.hermes`。

Windows：

| 文件 | 路径 | 级别 | 用途 |
|-----|------|------|------|
| agent.log | `%LOCALAPPDATA%\hermes\logs\agent.log` | INFO+ | Agent 完整运行日志 |
| errors.log | `%LOCALAPPDATA%\hermes\logs\errors.log` | WARNING+ | 仅警告和错误 |
| gateway.log | `%LOCALAPPDATA%\hermes\logs\gateway.log` | INFO+ | Gateway 消息路由 |
| gui.log | `%LOCALAPPDATA%\hermes\logs\gui.log` | INFO+ | 桌面 UI 连接 |
| main.log | `%APPDATA%\clawworker\logs\main.log` | info+ JSON | Laiye Worker 桌面端 bootstrap、Runtime 启动/更新、IPC 错误 |
| gateway_state.json | `%LOCALAPPDATA%\hermes\gateway_state.json` | JSON | Gateway 当前状态、PID、平台连接状态 |
| gateway.pid | `%LOCALAPPDATA%\hermes\gateway.pid` | JSON | Gateway 进程启动参数与 PID |

macOS：

| 文件 | 路径 | 级别 | 用途 |
|-----|------|------|------|
| agent.log | `${HERMES_HOME:-$HOME/.hermes}/logs/agent.log` | INFO+ | Agent 完整运行日志 |
| errors.log | `${HERMES_HOME:-$HOME/.hermes}/logs/errors.log` | WARNING+ | 仅警告和错误 |
| gateway.log | `${HERMES_HOME:-$HOME/.hermes}/logs/gateway.log` | INFO+ | Gateway 消息路由 |
| gui.log | `${HERMES_HOME:-$HOME/.hermes}/logs/gui.log` | INFO+ | 桌面 UI 连接 |
| main.log | `$HOME/Library/Logs/clawworker/main.log` | info+ JSON | Laiye Worker 桌面端 bootstrap、Runtime 启动/更新、IPC 错误 |
| error-stack.jsonl | `$HOME/Library/Logs/clawworker/error-stack.jsonl` | JSONL | 桌面端结构化错误 |
| perf-sample.jsonl | `$HOME/Library/Logs/clawworker/perf-sample.jsonl` | JSONL | API、IPC、渲染耗时 |
| ui-trace.jsonl | `$HOME/Library/Logs/clawworker/ui-trace.jsonl` | JSONL | UI trace |
| gateway_state.json | `${HERMES_HOME:-$HOME/.hermes}/gateway_state.json` | JSON | Gateway 当前状态、PID、平台连接状态 |
| gateway.pid | `${HERMES_HOME:-$HOME/.hermes}/gateway.pid` | JSON | Gateway 进程启动参数与 PID |

## 日志行格式

```
YYYY-MM-DD HH:MM:SS,mmm LEVEL [client-xxxxxxxx] module.submodule: message
```

桌面端 `main.log` 通常是 Electron log 前缀加 JSON payload：

```
[YYYY-MM-DD HH:MM:SS.mmm] [info|warn|error] {"event":"bootstrap.task.failed",...}
```

解析桌面端日志时优先读 JSON 字段：`event`、`area`、`taskType`、`taskStatus`、`progressPhase`、`errorCode`、`errorMessage`、`timestamp`。

### 时间戳
- 格式: `2026-06-23 11:48:58,583`
- 时区: 本地时间 (UTC+8)
- 精度: 毫秒

### 日志级别
| 级别 | 含义 |
|-----|------|
| INFO | 正常运行事件 |
| WARNING | 潜在问题，已自动处理或降级 |
| ERROR | 严重错误，导致功能失败 |

### Client UUID
- 格式: `[client-27a0a0ab-24d9-400a-aa2a-e1f1915494e4]`
- 每个用户对话 session 有独立 UUID
- 部分 Gateway 级别日志没有 client UUID

### 模块名
常见模块命名空间：

**Agent 层:**
- `agent.conversation_loop` — 对话循环（API 调用、重试）
- `agent.tool_executor` — 工具执行
- `agent.auxiliary_client` — 辅助模型客户端
- `agent.title_generator` — 会话标题生成
- `run_agent` — Session DB 操作

**工具层:**
- `tools.browser_tool` — 浏览器工具
- `tools.web_tools` — Web 提取/搜索
- `tools.terminal` — 终端执行
- `tools.url_safety` — URL 安全检查

**Gateway 层:**
- `gateway.run` — Gateway 启动/关闭
- `gateway.platforms.slack` — Slack 平台
- `gateway.platforms.wecom` — 企业微信
- `gateway.platforms.dingtalk` — 钉钉
- `gateway.platforms.feishu` — 飞书
- `gateway.platforms.weixin` — 微信
- `gateway.platforms.api_server` — 本地 API 服务器
- `gateway.memory_monitor` — 内存监控
- `gateway.channel_directory` — 频道目录

**Desktop 层:**
- `bootstrap.task.progress` — Runtime 安装、更新、启动进度
- `bootstrap.task.failed` — Runtime 安装、更新、启动失败
- `runtime.start.failed` / `chat.send.failed` — 运行时或聊天发送失败（如出现）

## 版本与状态字段

报告中必须区分这些来源：

| 字段 | 首选来源 | 说明 |
|---|---|---|
| 客户端版本号 | Windows: `Laiye Worker.exe` `FileVersion/ProductVersion`; macOS: `Laiye Worker.app/Contents/Info.plist` `CFBundleShortVersionString/CFBundleVersion` | 这是桌面客户端版本，不是 runtime 版本 |
| Agent 版本号 | `${HERMES_HOME}/hermes-agent/hermes_cli/__init__.py` | 读取 `__version__` 和 `__release_date__` |
| Agent commit | `${HERMES_HOME}/hermes-agent/.git/HEAD` 与 `.git/refs/heads/main` | HEAD 与 main 不同时都报告 |
| Runtime 版本 | `gateway_state.json`/`gateway.pid` argv 或 `runtimes\versions` 最新目录 | active 与 latest 可能不同 |
| Gateway 状态 | `gateway_state.json` | 报告 `gateway_state`、PID、PID 存活、`updated_at`、platform states |
| 模型信息 | `agent.log` 中 `provider=... model=... platform=... base_url=...` | 标注来源是当前 session、当前窗口，还是最近已知 |
| Browser/Web 运行环境 | 在问题发生电脑上运行 `node --version`、`npm --version`、`npx --version`；离线附件复盘才优先使用 desktop `main.log`/JSONL bootstrap、gateway pid/argv 和日志错误 | 浏览器自动化失败时必须检查，npm 组件缺失是 managed web runtime 不可达的常见底层原因；本技能默认命令结果来自问题电脑现场 |

模型字段常见位置：

```text
agent.turn_context: conversation turn: session=... model=... provider=... platform=...
run_agent: OpenAI client created (...) provider=... base_url=... model=...
agent.conversation_loop: Turn ended: reason=... model=... api_calls=...
```

## 错误类型速查

### content_policy_violation (HTTP 400)
```
ERROR agent.conversation_loop: Non-retryable client error: Error code: 400 - 
{'error': {'message': 'Content policy violation', ...}}
```
**含义**: 消息内容被模型提供商的安全审核拦截。
**影响**: 当前对话立即终止，无法恢复。
**常见触发**: 中文政治敏感词、色情内容、暴力描述。
**变体**: 同一错误也可能出现在 `agent.title_generator` 模块（会话标题自动生成被拦截），此变体不影响对话功能，仅标题缺失。

### CDP Endpoint Resolution Failure
```
WARNING tools.browser_tool: Failed to resolve CDP endpoint http://127.0.0.1:30191 
via http://127.0.0.1:30191/json/version: 502 Server Error
```
**含义**: Chrome DevTools Protocol 端口未就绪或浏览器未完全启动。
**影响**: browser_navigate、browser_click 等操作失败。

### Managed Web Runtime Unreachable
```
WARNING hermes_plugins.web__clawworker: clawworker_managed.web.capabilities.failed status=None code=managed_web_runtime_unreachable
```
**含义**: Agent 探测 managed web runtime 能力失败，web/browser 能力不可用。
**影响**: browser automation、web search/extract 等 managed web 能力不可用；Gateway api_server 和模型链路可能仍正常。
**判断**: 这通常是症状，不是最底层根因。必须继续检查 desktop `main.log`、`error-stack.jsonl` 和 bootstrap 安装阶段日志，尤其是 npm/node/npx/playwright 组件缺失或安装失败。

### Browser Automation Dependency Missing
```
npm: command not found
npx: command not found
Cannot find module ...
MODULE_NOT_FOUND
ENOENT ... npm
bootstrap.task.failed ... npm ...
```
**含义**: 浏览器自动化运行环境缺少 npm/node/npx 组件，或相关 npm 包安装失败。
**影响**: managed web runtime 无法启动，随后 Agent 侧会看到 `managed_web_runtime_unreachable` 或 `web.capabilities.failed`。
**判断**: 如果同一时间窗口同时出现 npm 组件缺失和 managed web runtime 不可达，报告根因应写“运行环境缺失 npm 组件”，后者写为直接影响/症状。
**修复方向**: 修复或重装 npm/node/npx 与浏览器自动化依赖，重启客户端，再确认 web capabilities 是否恢复。

### Tool Execution Failure
```
WARNING agent.tool_executor: Tool terminal returned error (2.20s): 
{"output": "...No such file or directory", "exit_code": 126, "error": null}
```
**含义**: 工具执行返回非零退出码。
**影响**: 单个工具调用失败，Agent 可能重试其他路径。

### Auxiliary Client Unavailable
```
WARNING agent.auxiliary_client: Auxiliary auto-detect: no provider available
```
**含义**: 用于压缩/摘要/记忆管理的辅助推理模型不可用。
**影响**: 上下文压缩和记忆管理功能降级。

### Session DB Write Failure
```
WARNING run_agent: Session DB append_message failed: FOREIGN KEY constraint failed
```
**含义**: SQLite 会话数据库写入冲突。
**影响**: 该条消息可能未持久化到 session 历史。

### Platform Connection Issues
```
WARNING gateway.platforms.slack: Socket Mode unhealthy (transport disconnected); reconnecting
```
**含义**: 消息平台 WebSocket 连接断开。
**影响**: 该平台的消息收发暂时中断，Gateway 会自动重连。

### Gateway State Running But Platform Degraded
```
gateway_state=running; api_server=connected; slack/wecom/dingtalk=disconnected|reconnecting
```
**含义**: Gateway 进程整体仍在运行，但某个平台连接异常。
**影响**: 本地桌面对话通常仍可用；受影响平台的消息收发可能中断。
**判断**: 不要把单个平台断连归因成 Gateway 整体崩溃。必须同时报告 `gateway_state`、PID 存活和平台状态。

### Desktop Runtime Bootstrap Failure
```
bootstrap.task.failed errorCode=runtime_pack_update_ports_busy
bootstrap.task.failed errorCode=runtime_policy_external_start_disabled
bootstrap.task.failed errorCode=hermes_not_installed
bootstrap.task.failed ... npm ...
```
**含义**: Laiye Worker 桌面端启动、安装或更新 Agent Runtime 失败。
**影响**: 可能导致客户端无法启动 Agent、无法更新 Runtime，或继续使用外部 Agent。
**判断**: Windows 从 `%APPDATA%\clawworker\logs\main.log` 读取；macOS 从 `$HOME/Library/Logs/clawworker/main.log` 和 JSONL telemetry 读取。重点字段是 `errorCode`、`errorMessage`、`progressPhase`。如果失败发生在 browser/web 自动化安装阶段，优先确认 npm/node/npx/playwright 是否缺失。

### URL Safety Block
```
WARNING tools.url_safety: Blocked request to private/internal address: github.com -> 198.18.0.97
```
**含义**: DNS 解析到内网地址，被安全策略拦截。
**影响**: 该 URL 请求被阻止。

### TCP Connection Timeout (os error 10060)
```
WARNING tools.browser_tool: browser_navigate error: os error 10060
```
**含义**: Windows 网络层无法建立 TCP 连接（目标不可达、代理阻断、或防火墙拦截）。
**影响**: browser_navigate 及任何依赖出站 TCP 的工具操作失败。
**常见原因**: VPN 断开、HTTP 代理配置错误、目标服务器不可达、DNS 污染。

### Tool Execution Interrupted
```
read_file: [Command interrupted]
```
**含义**: 工具执行被外部信号中断（用户取消、超时、或进程被杀）。
**影响**: 单个工具调用返回不完整结果，不影响后续操作。
**判断**: 若同一 session 中出现多次，检查是否存在死循环或超时配置过短的问题。

### Streaming Delivery Failure
```
ERROR agent.conversation_loop: Streaming failed before delivery: ...
```
**含义**: Provider 流式响应在生成完成后、传输到客户端前失败。与 Content Policy Violation 不同（后者是 Provider 主动拒绝），此类是传输层中断。
**影响**: 模型已生成回复但用户未收到，表现为"说到一半断掉"。
**常见原因**: Provider 服务不稳定、网络闪断、反向代理超时。
