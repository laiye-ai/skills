---
name: laiye-worker-diagnostics
description: 仅当用户明确要求使用或运行客户端日志诊断技能，或点名 laiye-worker-diagnostics，并希望基于 Laiye Worker/Hermes Agent 本机日志生成诊断报告时使用。用于现场读取日志并输出客户端版本、Agent 版本、Gateway 状态、模型信息、错误信息和详细原因分析；不要因普通客户端使用、一般报错咨询、模型信息查询或 gateway 状态询问自动触发。
triggers:
  - 使用客户端日志诊断技能
  - 运行客户端日志诊断技能
  - 生成客户端日志诊断报告
  - 使用 laiye-worker-diagnostics
  - 运行 laiye-worker-diagnostics
  - 用客户端日志诊断 skill 排查
  - 用 Laiye Worker 日志诊断技能查问题
metadata:
  requires:
    bins: ["python3"]
---

# Laiye Worker 诊断 Skill

根据 Laiye Worker 客户端和 Hermes Agent 日志，调查最近对话中发生的问题。目标不是只找一条报错，而是还原“用户请求 -> 客户端/网关 -> Agent -> 模型/工具 -> 错误”的完整链路，并解释为什么会发生。

本技能默认且优先在**问题发生的那台电脑**上运行。自动采集脚本读取的客户端版本、Agent 版本、Gateway 状态、Node/npm/npx 和 OS 信息应视为目标机器现场状态。只有在用户明确提供离线日志附件、或为了复盘把日志拷贝到临时目录时，才把命令结果标为“诊断机器环境，仅供参考”。

## 触发边界

只有用户明确要求“使用/运行客户端日志诊断技能”“生成客户端日志诊断报告”或点名 `laiye-worker-diagnostics` 时，才使用本技能。不要因为用户只是提到“客户端”“日志”“报错”“模型信息”“Gateway 状态”“Agent 版本”等泛词就触发本技能；这类问题先按普通问答或轻量排查处理。

## 工具能力边界

先确认当前对话可用工具，再选择采集路径。若 `terminal` / `execute_code` / 本地命令执行能力不可用，不要把“终端工具不可用”当作客户端问题根因，也不要反复尝试运行脚本；直接改用 `session_search`、`search_files`、`read_file` 做纯日志调查，并在最终报告的证据边界中说明“自动采集脚本未运行，部分版本/PID/进程存活字段证据不足”。

## 日志位置

不要写死某台机器的用户名或绝对路径。优先使用 `HERMES_HOME`；未设置时 Windows 默认 `%LOCALAPPDATA%\hermes`，macOS/Linux 默认 `~/.hermes`。

Windows：

| 日志文件 | 路径 | 内容 |
|---------|------|------|
| agent.log | `%LOCALAPPDATA%\hermes\logs\agent.log` | Agent 运行日志，含 session、tool 调用、插件注册 |
| errors.log | `%LOCALAPPDATA%\hermes\logs\errors.log` | WARNING+ERROR 汇总 |
| gateway.log | `%LOCALAPPDATA%\hermes\logs\gateway.log` | Gateway 启动、平台连接、API 请求 |
| gui.log | `%LOCALAPPDATA%\hermes\logs\gui.log` | GUI/桌面端 WebSocket 连接记录 |
| desktop main.log | `%APPDATA%\clawworker\logs\main.log` | Laiye Worker 桌面端 bootstrap、Runtime 启动/更新、IPC 错误 |
| gateway_state.json | `%LOCALAPPDATA%\hermes\gateway_state.json` | Gateway 当前状态、PID、平台连接状态 |
| gateway.pid | `%LOCALAPPDATA%\hermes\gateway.pid` | Gateway 进程启动参数与 PID |

macOS：

| 日志文件 | 路径 | 内容 |
|---------|------|------|
| agent.log | `${HERMES_HOME:-$HOME/.hermes}/logs/agent.log` | Agent 运行日志，含 session、tool 调用、插件注册 |
| errors.log | `${HERMES_HOME:-$HOME/.hermes}/logs/errors.log` | WARNING+ERROR 汇总 |
| gateway.log | `${HERMES_HOME:-$HOME/.hermes}/logs/gateway.log` | Gateway 启动、平台连接、API 请求 |
| gui.log | `${HERMES_HOME:-$HOME/.hermes}/logs/gui.log` | GUI/桌面端 WebSocket 连接记录 |
| desktop main.log | `$HOME/Library/Logs/clawworker/main.log` | Laiye Worker 桌面端 bootstrap、Runtime 启动/更新、IPC 错误 |
| desktop JSONL | `$HOME/Library/Logs/clawworker/error-stack.jsonl` 等 | 桌面端结构化错误、性能、UI trace |
| gateway_state.json | `${HERMES_HOME:-$HOME/.hermes}/gateway_state.json` | Gateway 当前状态、PID、平台连接状态 |
| gateway.pid | `${HERMES_HOME:-$HOME/.hermes}/gateway.pid` | Gateway 进程启动参数与 PID |

备用 Agent 日志（Windows 也可能存在）：
| 日志文件 | 路径 |
|---------|------|
| agent.log | `%USERPROFILE%\.hermes\logs\agent.log` |
| errors.log | `%USERPROFILE%\.hermes\logs\errors.log` |

## 采集路径

如果当前对话提供 `terminal` 或等价本地命令执行工具，优先运行 bundled script 生成初始报告，再用 `session_search`、`search_files`、`read_file` 补充证据。脚本只读本机日志和元数据，会隐藏完整 prompt/response、token、cookie、密钥等敏感内容。

如果当前对话提示“终端工具不可用”或没有本地命令执行工具，跳过脚本，直接执行后续纯文件搜索流程。此时仍应完成诊断报告，但对需要命令才能确认的字段（如 EXE 文件版本、PID 是否存活、Node/npm/npx 命令版本）标注“未找到”或“终端不可用，证据不足”。

Windows PowerShell：

```powershell
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
python "$HermesHome\skills\debug\laiye-worker-diagnostics\scripts\collect_client_diagnostics.py" --minutes 60
```

macOS bash/zsh：

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
python "$HERMES_HOME/skills/debug/laiye-worker-diagnostics/scripts/collect_client_diagnostics.py" --minutes 60
```

常用参数：

```powershell
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }

# 指定更精确的时间窗口
python "$HermesHome\skills\debug\laiye-worker-diagnostics\scripts\collect_client_diagnostics.py" --since "2026-06-24 10:30" --until "2026-06-24 10:50"

# 已知 session/client/run id 时精确聚焦
python "$HermesHome\skills\debug\laiye-worker-diagnostics\scripts\collect_client_diagnostics.py" --session client-xxxxxxxx

# 需要结构化数据做二次分析时
python "$HermesHome\skills\debug\laiye-worker-diagnostics\scripts\collect_client_diagnostics.py" --minutes 180 --json
```

脚本报告必须作为初稿，不是最终结论。若脚本显示“调查窗口内未找到模型请求”“未定位到 session”“未归类错误”，继续按下面步骤人工复核。

## 日志格式

```
YYYY-MM-DD HH:MM:SS,mmm LEVEL [client-UUID] module: message
```

- 时间戳精确到毫秒
- LEVEL: INFO / WARNING / ERROR
- `[client-UUID]`: 会话级标识，用于关联同一对话的所有日志行
- module: 形如 `agent.tool_executor`、`gateway.platforms.slack`

遇到不熟悉的模块、版本字段或错误模式时，读取 [references/log-format.md](references/log-format.md)。

## 调查步骤

### 第零步：选择采集方式

如果有 `terminal` 或等价本地命令执行工具，先运行 `scripts/collect_client_diagnostics.py`。从脚本输出中确认：

- 客户端版本号是否来自 `Laiye Worker.exe` 文件版本。
- Agent 版本号是否包含 `hermes_cli.__version__`、release date、Git HEAD/main commit。
- Runtime 版本目录是否包含 active/latest。
- Gateway 状态是否包含 `gateway_state`、PID、PID 是否存活、平台状态。
- 模型信息是否包含 provider、model、platform、base_url host、最近 turn 结束原因。
- 涉及 browser/web 自动化时，环境信息是否包含 Node.js、npm、npx，且是否存在 npm 组件缺失或安装失败。
- 错误信息是否按时间列出，并已去重。
- 根因分析是否解释了直接原因、深层原因、影响范围、建议处理。

如果脚本报告已经能解释问题，也仍要抽查 1-3 条关键日志，确认结论不是误分类。

如果没有 `terminal` / `execute_code`：

- 不运行 bundled script。
- 从 `errors.log`、`agent.log`、`gateway.log`、`gui.log`、desktop `main.log` 直接搜索和读取证据。
- 读取 `gateway_state.json`、`gateway.pid`、`${HERMES_HOME}/hermes-agent/hermes_cli/__init__.py` 等文本元数据。
- 无法通过命令确认的字段要显式写“终端不可用，证据不足”，不能省略。

### 第零点五步：用 session_search 发现异常会话

如果需要定位具体对话，使用 `session_search` 查看最近是否有已记录的异常：

```
session_search(query="error ERROR 报错 失败", sort="newest", limit=5)
```

这会返回最近 5 个含错误关键词的会话摘要。从结果中可以：
- 获取 `session_id`（即 client UUID），后续在日志中用它精确过滤
- 查看 `snippet` 了解错误类型（content_policy_violation、CDP 502、FOREIGN KEY 等）
- 从 `bookend_start`/`bookend_end` 理解用户意图和最终结果

如果 `session_search` 无结果，说明错误可能未被持久化到 DB，或发生在 session 创建/落库之前。继续从日志文件和自动采集报告入手。

### 第一步：确定时间范围

时间范围是后续所有搜索的基础。确定方法（按优先级）：

1. **用户明确指定**：如"今天上午 10 点的报错"、"刚才那个错误"
2. **从 gui.log 推断会话启动时间**：搜索最近一次 `ws accepted` 时间戳。`path` 使用当前平台的 `HERMES_HOME/logs`。
   ```python
   search_files(
       pattern="ws accepted",
       path="<HERMES_HOME>/logs",
       file_glob="gui.log",
       output_mode="content"
   )
   ```
3. **从 errors.log 找最近错误时间**：
   ```python
   search_files(
       pattern="ERROR|WARNING",
       path="<HERMES_HOME>/logs",
       file_glob="errors.log",
       output_mode="content"
   )
   ```
   从结果中提取最早和最晚时间戳作为调查窗口。
4. **默认窗口**：最近 1 小时内。如果日志跨度过大（如全天），用 `search_files` 的 `output_mode="count"` 先估算密度，再决定是否需要缩小时间窗口。

关键原则：**宁可从宽窗口开始，再用 session UUID 精确收敛**。不要试图一次 read_file 读完整个日志文件。

### 第二步：提取版本、Gateway 和模型信息

报告必须同时区分桌面客户端、Agent、Runtime 和模型链路，不要把 runtime 目录误写成客户端版本。

1. **客户端版本号**：Windows 优先读取 `Laiye Worker.exe` 的 `FileVersion`/`ProductVersion`；macOS 优先读取 `/Applications/Laiye Worker.app/Contents/Info.plist` 或 `~/Applications/...` 的 `CFBundleShortVersionString`/`CFBundleVersion`。
2. **Agent 版本号**：读取 `${HERMES_HOME}/hermes-agent/hermes_cli/__init__.py` 中的 `__version__` 和 `__release_date__`。
3. **Agent Git commit**：读取 `${HERMES_HOME}/hermes-agent/.git/HEAD` 指向的 commit；同时读取 `.git/refs/heads/main`，两者不同则都报告。
4. **Runtime 版本**：从 `${HERMES_HOME}/gateway.pid`/`gateway_state.json` 的 argv 推断 active runtime；再列出 `${HERMES_HOME}/runtimes/versions` 最新目录。
5. **Gateway 状态**：读取 `gateway_state.json` 的 `gateway_state`、`pid`、`updated_at`、`active_agents`、各平台 `state/error_message`；必要时检查 PID 是否存活。
6. **模型信息**：从 `agent.log` 中搜索 `provider=`、`model=`、`platform=`、`base_url=`、`Turn ended: reason=`，优先使用当前 session/时间窗口内的记录；如果窗口内没有模型请求，只能标注“最近已知模型信息”。
7. **环境版本**：OS、Python；如涉及 browser/web/tools，必须同时检查 Node.js、npm、npx。本技能默认在问题发生电脑上运行，因此这些命令结果就是目标机器现场状态；只有在离线日志附件或临时目录复盘时，才改用 desktop main.log、JSONL bootstrap、gateway pid/argv 或日志中的安装错误判断目标机器状态。

如果 terminal 可用，执行以下命令获取准确版本：
```powershell
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }

# Windows 客户端版本
(Get-Item "$env:LOCALAPPDATA\Programs\Claw Worker\Laiye Worker\Laiye Worker.exe").VersionInfo |
  Select-Object FileVersion,ProductVersion,ProductName

# Agent 版本
Get-Content "$HermesHome\hermes-agent\hermes_cli\__init__.py" |
  Select-String "__version__|__release_date__"

# Git commit
Get-Content "$HermesHome\hermes-agent\.git\HEAD"
Get-Content "$HermesHome\hermes-agent\.git\refs\heads\main" -ErrorAction SilentlyContinue

# 运行时版本
Get-ChildItem "$HermesHome\runtimes\versions" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 Name,LastWriteTime

# Gateway 状态
Get-Content "$HermesHome\gateway_state.json"

# Python 版本
python --version

# Node 版本（如涉及 browser tools）
node --version 2>$null
npm --version 2>$null
npx --version 2>$null
```

macOS 版本命令：

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# macOS 客户端版本
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "/Applications/Laiye Worker.app/Contents/Info.plist" 2>/dev/null
/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "/Applications/Laiye Worker.app/Contents/Info.plist" 2>/dev/null

# Agent 版本
grep -E "__version__|__release_date__" "$HERMES_HOME/hermes-agent/hermes_cli/__init__.py"

# Git commit
cat "$HERMES_HOME/hermes-agent/.git/HEAD"
cat "$HERMES_HOME/hermes-agent/.git/refs/heads/main" 2>/dev/null

# Runtime 和 Gateway
ls -1t "$HERMES_HOME/runtimes/versions" 2>/dev/null | head -1
cat "$HERMES_HOME/gateway_state.json" 2>/dev/null

# Node/npm/npx（如涉及 browser/web 自动化）
node --version 2>/dev/null
npm --version 2>/dev/null
npx --version 2>/dev/null
```

### 第三步：用 search_files 定位错误

不要用 terminal grep（Windows bash 环境可能不支持大文件或中文），改用 `search_files` 工具：

下面示例中的 `<HERMES_HOME>` 表示当前 Agent home：Windows 通常是 `%LOCALAPPDATA%\hermes`，macOS 通常是 `$HOME/.hermes`，如果设置了 `HERMES_HOME` 则以该环境变量为准。

```python
# 搜索今天的所有 ERROR/WARNING（替换日期为当天）
search_files(
    pattern="2026-06-23.*(ERROR|WARNING)",
    path="<HERMES_HOME>/logs",
    file_glob="errors.log",
    output_mode="content"
)
```

如果 errors.log 为空或没有匹配，说明错误可能只在 agent.log 里（某些错误以 INFO 级别记录），扩大搜索。常见原因：错误被记作 INFO 而非 ERROR（如 CDP 超时有时只记 INFO）、或发生在 session 持久化之前的早期阶段（如 provider 连接失败在 conversation_loop 初始化时）。

在 agent.log 中搜索常见错误关键词：

```python
# 在 agent.log 中搜索常见错误关键词
search_files(
    pattern="(content.policy|CDP endpoint|FOREIGN KEY|auxiliary_client.*no provider|Blocked request|returned error|managed_web_runtime_unreachable|web.capabilities.failed|Received UNKNOWN)",
    path="<HERMES_HOME>/logs",
    file_glob="agent.log",
    output_mode="content"
)
```

浏览器自动化安装或 web capability 失败时，必须继续搜索 npm/node/npx 安装依赖。`managed_web_runtime_unreachable` 只是 Agent 侧看到的上层症状，不能直接当最终根因。

```python
search_files(
    pattern="(npm|node|npx|playwright|browser.*install|managed_web|web.capabilities|component.*missing|MODULE_NOT_FOUND|ENOENT|not found)",
    path="<HERMES_HOME>/logs",
    file_glob="*.log",
    output_mode="content"
)
```

同时检查桌面 bootstrap 日志：Windows 的 `%APPDATA%\clawworker\logs\main.log`，macOS 的 `$HOME/Library/Logs/clawworker/main.log`、`error-stack.jsonl`。重点字段是 `bootstrap.task.failed`、`errorCode`、`errorMessage`、`progressPhase`；如果这些日志显示 npm 组件缺失或安装失败，报告根因必须写为“运行环境缺失 npm 组件”，并把 `managed_web_runtime_unreachable` 写成后续影响。

如果已经通过 session_search 获得了 client UUID，直接精确过滤：

```python
search_files(
    pattern="client-27a0a0ab",
    path="<HERMES_HOME>/logs",
    file_glob="errors.log",
    output_mode="content"
)
```

重点关注以下错误类型：
- `agent.conversation_loop` — API 调用失败（provider 错误、超时、内容审核）
- `agent.tool_executor` — 工具执行失败（terminal、browser、read_file 等）
- `tools.browser_tool` — 浏览器/CDP 连接失败
- `hermes_plugins.web__clawworker` — managed web capability 探测失败，常见上游原因包括 npm/node/npx 组件缺失
- `gateway.platforms` — 平台连接异常
- `gateway.run` — 关注 `Received UNKNOWN`（桌面端主动替换子进程触发重启，常见于空闲恢复场景）
- `agent.auxiliary_client` — 辅助模型不可用
- `run_agent` — Session DB 写入失败

### 第四步：串联上下文（最重要的步骤）

拿到错误行后，需要还原完整事件链。不要直接 `read_file` 整个日志（可能几百 MB），而是用精确策略：

**4a. 提取关键信息**

从错误行中提取：
- 时间戳（精确到毫秒）
- `[client-UUID]`（关联同一对话的所有日志行）
- 错误模块和消息

**4b. 用 search_files 获取该 session 完整时间线**

```python
search_files(
    pattern="client-UUID",
    path="<HERMES_HOME>/logs",
    file_glob="agent.log",
    output_mode="content"
)
```

如果结果太多（超过 50 行），说明该 session 日志量大，改用时间窗口精确定位：

**4c. 用 read_file + offset 按时间窗口读取**

日志按时间顺序写入，可以通过计算粗略行偏移来读取特定时间段：

```python
# 先用 search_files output_mode="count" 估计行数
search_files(
    pattern="client-UUID",
    path="<HERMES_HOME>/logs",
    file_glob="agent.log",
    output_mode="count"
)
# 假设返回 ~200 行，错误在中间位置，读取中间 100 行
read_file(
    path="<HERMES_HOME>/logs/agent.log",
    offset=50,
    limit=100
)
```

**4d. 用 execute_code 做复杂跨文件关联（高级）**

当需要在 agent.log、errors.log、gateway.log 三者之间关联时，用 `execute_code` 写 Python 脚本：

```python
from hermes_tools import search_files, read_file

# 1. 从 errors.log 获取所有今日错误
# 2. 提取每个错误的 client UUID 和时间戳
# 3. 在 agent.log 中查找对应的工具调用链
# 4. 在 gateway.log 中检查 Gateway 是否正常运行
# 5. 输出结构化的事件链 JSON
```

**4e. 结合 session_search 验证**

用 client UUID 反向查 session_search 确认：
```python
session_search(
    session_id="client-xxxxxxxx",
    profile="default"
)
```

这会返回该 session 的用户消息和 assistant 回复，帮助你理解"用户当时在做什么 → 哪里出错了"。

**4f. 还原完整事件链**

综合以上信息，按时间排列：
用户消息摘要/长度 → 工具调用1 → 工具结果1 → 工具调用2 → 工具结果2 → ... → 错误发生点

### 第五步：输出诊断报告

报告必须包含以下部分（用中文输出）。任何字段没有找到时，明确写“未找到”或“证据不足”，不要省略。

```
# 客户端问题诊断报告

## 结论
- 初步根因：
- 置信度：
- 直接原因：
- 深层原因分析：
- 影响范围：
- 建议处理：

## 版本信息
- 客户端版本号：
- 客户端产品/安装路径：
- Agent 版本号：
- Agent Git HEAD/main：
- Runtime 版本：
- Python/Node/npm/npx/OS：

## Gateway 状态
- gateway_state：
- PID/进程存活：
- updated_at：
- active_agents：
- 平台状态：
- 近窗口 Gateway 异常：

## 模型信息
- Provider：
- Model：
- Platform：
- Base URL Host：
- 最近模型请求时间：
- 最近 Turn 结束原因：
- 信息来源：当前 session / 当前窗口 / 最近已知

## 问题时间与 Session
- 调查窗口：
- 关联 Session ID：
- 用户意图摘要：只写摘要，不粘贴完整 prompt

## 事件链
- [时间] 用户消息摘要/长度：
- [时间] 工具调用：
- [时间] 模型请求：
- [时间] 错误发生：
- [时间] 后续恢复/重试：

## 错误信息
- 错误类型：
- 错误模块：
- 原始错误消息：保留关键英文错误；隐藏 token、完整 prompt/response、文件内容
- HTTP 状态码/异常码：
- 出现次数：

## 错误原因分析
- 证据：
- 直接原因：
- 深层原因：
- 排除项：
- 是否瞬态：
- 影响范围：

## 建议修复
1. ...
2. ...
```

## 实际调查案例

### 案例：对话突然报错断连 — Content Policy Violation

**用户描述**：刚才问了一个问题，AI 回复到一半就断了，显示报错。

**调查过程（纯 Hermes 工具）**：

1. session_search 发现最近 session 有 error：
   ```
   session_search(query="error ERROR", sort="newest", limit=3)
   → 发现 client-27a0a0ab session snippet 含 "Content policy violation"
   ```

2. search_files 在 errors.log 中确认：
   ```
   pattern="client-27a0a0ab.*ERROR"
   path="<HERMES_HOME>/logs"
   file_glob="errors.log"
   → 2026-06-23 11:48:58,583 ERROR [client-27a0a0ab] agent.conversation_loop:
     Non-retryable client error: Error code: 400 -
     {'error': {'message': 'Content policy violation', 'type': 'invalid_request_error'}}
   ```

3. search_files 获取该 session 完整工具链：
   ```
   pattern="client-27a0a0ab"
   path="<HERMES_HOME>/logs"
   file_glob="agent.log"
   limit=100
   → 显示用户消息 → web_search(open-apa) → write_file → 模型返回时触发审核
   ```

4. session_search 读取完整对话：
   ```
   session_search(session_id="client-27a0a0ab")
   → 用户消息包含 GitHub 链接 + "定位问题"，模型回复包含可疑内容被拦截
   ```

**诊断结果**：用户消息本身没有敏感词，但模型生成的回复中包含链接列表，被提供商安全审核误判为恶意内容。HTTP 400，不可重试，对话直接终止。

### 案例：browser tool 超时无响应

**用户描述**：让 AI 打开网页，一直卡住不动。

**调查过程**：

1. session_search → 找到该 session，snippet 含 "CDP"
2. search_files errors.log 确认：
   ```
   pattern="CDP endpoint.*502|browser.*timeout"
   → WARNING tools.browser_tool: Failed to resolve CDP endpoint: 502 Server Error
   ```
3. agent.log 中搜索该 session → Chrome 启动后立即崩溃 (exit code 21)
4. 检查 gateway.log → 确认 Gateway 正常运行，非平台问题

**诊断结果**：Chrome 浏览器启动失败，CDP 端口未就绪，所有 browser 操作超时。Agent 正确回退到其他方案。

| 错误模式 | 根因 | 修复方向 |
|---------|------|---------|
| `Content policy violation` / HTTP 400 | 模型提供商内容审核拦截 | 检查消息内容是否触发安全策略；可考虑换 provider |
| `Failed to resolve CDP endpoint` / 502 | 浏览器 Chrome DevTools 端口未就绪 | 等待 browser tool 启动完成或重启 |
| `managed_web_runtime_unreachable` / `web.capabilities.failed` | managed web runtime 未启动或不可达；需要继续查安装日志 | 检查 desktop main.log/bootstrap JSONL，优先确认 npm/node/npx/playwright 组件是否缺失 |
| `npm`/`node`/`npx` not found、`MODULE_NOT_FOUND`、`ENOENT` | 浏览器自动化运行环境缺少 npm 组件或依赖安装失败 | 修复/重装 npm 组件与浏览器自动化依赖后重启客户端 |
| `No inference provider configured` | 缺少 API key 或 provider 未配置 | `hermes model` 设置 provider |
| `auxiliary_client ... no provider available` | 辅助模型（压缩/摘要）不可用 | 配置 OPENROUTER_API_KEY 或本地模型 |
| `FOREIGN KEY constraint failed` (Session DB) | Session 数据库写入冲突 | 通常是暂时性问题，重试即可 |
| `terminal returned error ... No such file or directory` | bash shell 路径转换问题（Windows 特有问题） | 使用 MSYS 风格路径 `/c/Users/...` |
| `Blocked request to private/internal address` | URL 安全检查拦截内网地址 | 确认目标地址是否为合法公网地址 |
| `os error 10060` / TCP 连接超时 | Windows 网络层连接失败（目标不可达或代理阻断） | 检查网络代理、VPN、目标地址可达性 |
| `[Command interrupted]` 出现在工具输出中 | 工具执行被用户操作或超时机制中断 | 通常为瞬态，重试即可；高频出现则检查工具超时配置 |
| `Streaming failed before delivery` | 提供商流式响应在传输阶段中断，回复未送达用户 | 与 Content Policy Violation 不同，可能是连接中断而非内容审核；检查 Provider 稳定性 |
| `title_generator.*Content policy violation` | 会话标题自动生成被内容审核拦截（独立于对话主流程） | 不影响对话功能，仅标题缺失；检查 Provider 的安全策略 |
| Slack/DingTalk/WeCom 连接失败 | 平台 token 过期或网络代理问题 | 检查代理设置和 token |
| `signal=UNKNOWN` Gateway 计划内重启（启动慢 > 2 分钟） | 桌面端从长时间空闲（数小时无 "node" UA 健康检查）恢复后主动替换 Gateway 子进程；旧进程优雅退出 + 新 Python 冷启动（42+ 插件导入）耗时 2-3 分钟 | 确认是预期行为：agent.log 搜 `"node"` UA 请求找空闲断点，gateway.log 搜 `Received UNKNOWN` 确认触发源为桌面端（`parent_pid`）。详见 [references/gateway-idle-restart-pattern.md](references/gateway-idle-restart-pattern.md) |

## 实施要点

1. **不要猜测，必须从实际日志文件或本机元数据中提取数据**。客户端版本、Agent 版本、Gateway 状态、模型信息、错误消息都必须能溯源。
2. **区分瞬态错误和持续故障**：
   - **瞬态**：同一 session 内只出现 1-2 次，后续相同操作成功。如 `FOREIGN KEY constraint failed`（重试后成功）、`os error 10060`（同一 URL 第二次可达）、`[Command interrupted]`（用户重发消息后正常）。此类无需修复，仅记录备查。
   - **持续故障**：同一 session 内重复出现 3 次以上，或跨多个 session 持续出现。如 `Content policy violation` 对同一消息每次返回、`CDP endpoint` 反复 502。此类需深入排查并给出修复建议。
   - **判断方法**：对比同一错误在同 session 中的出现次数，以及跨 session 对比（用 session_search 看近期是否有相同模式）。
3. **优先顺序**：
   - terminal 可用：`scripts/collect_client_diagnostics.py` → 生成初始报告、版本信息、Gateway 状态、模型信息、错误去重、初步根因。
   - terminal 不可用：跳过脚本，从 `session_search`、`search_files`、`read_file` 开始。
   - `session_search` → 发现异常会话（最快，无需读文件）。
   - `search_files` → 在日志中搜索模式（支持正则，比 bash grep 更可靠）。
   - `read_file` with offset/limit → 按时间窗口读取（避免一次性加载超大日志）。
   - `execute_code` → 跨文件关联分析（agent.log + errors.log + gateway.log 联合查询）。
   - terminal grep 仅在 terminal 可用时作为最后备选（Windows bash 环境 hex 字符处理有坑）。
4. **隐私和脱敏**：不要在报告中粘贴完整 prompt、完整 response、文件内容、token、cookie、authorization header、API key。用户消息只做意图摘要或长度说明。
5. **模型信息要标注证据范围**：当前 session > 当前时间窗口 > 最近已知。不能把“最近已知模型”写成“本次错误一定使用的模型”。
6. **Gateway 判断要区分整体状态和平台状态**：`gateway_state=running` 且 PID 存活时，Gateway 整体未崩溃；Slack/DingTalk/WeCom/Feishu 等平台断连只影响对应平台，除非 api_server 也异常。
7. **优先读 `errors.log`**（包含所有 WARNING+ 级别，体积小），再按需读 `agent.log` 特定 session。
8. 如果用户未指定时间，默认调查最近 1 小时内的问题。
9. 报告使用中文，但保留原始英文错误消息以便对照。
10. 如果日志中没有明显错误，检查 `gateway.log`、`desktop main.log` 和 `gateway_state.json` 确认 Gateway/Runtime 是否正常启动和运行。对于"启动慢"类问题，重点检查 gateway.log 中的 `Received UNKNOWN` 记录和 agent.log 中 `"node"` UA 空闲间隔——这能区分"Gateway 异常崩溃"和"桌面端空闲恢复主动替换"。
11. 如果看到 `managed_web_runtime_unreachable`、`web.capabilities.failed` 或浏览器自动化安装失败，必须继续查 npm/node/npx/playwright 安装日志；只有缺少这些证据时，才把根因写为“managed web runtime 不可达（底层原因证据不足）”。
12. 如果 `session_search` 无结果但用户明确说刚才有错误，说明错误可能发生在 session 持久化之前（如 provider 拒绝、连接中断、客户端启动失败），此时必须直接从日志文件入手。

