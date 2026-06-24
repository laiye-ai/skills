#!/usr/bin/env python3
"""Collect local Laiye Worker / Hermes Agent diagnostics from client logs.

Read-only helper for the laiye-worker-diagnostics skill. It emits a sanitized
Chinese Markdown report by default and never prints full prompts, responses,
tokens, cookies, or file contents.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>INFO|WARNING|ERROR|DEBUG|CRITICAL) "
    r"(?:(?P<session>\[[^\]]+\]) )?"
    r"(?P<module>[\w.\-]+): (?P<message>.*)$"
)
DESKTOP_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] "
    r"\[(?P<level>\w+)\]\s+(?P<message>.*)$"
)
SESSION_RE = re.compile(r"\b(?:client|run|session)_[a-f0-9]{8,}\b|\bclient-[a-f0-9-]{8,}\b", re.I)
MODEL_RE = re.compile(r"\bmodel=([^\s,]+)")
PROVIDER_RE = re.compile(r"\bprovider=([^\s,]+)")
PLATFORM_RE = re.compile(r"\bplatform=([^\s,]+)")
BASE_URL_RE = re.compile(r"\bbase_url=([^\s,]+)")
TURN_REASON_RE = re.compile(r"\bTurn ended: reason=([^\s]+)")

ERROR_PATTERNS: list[dict[str, str]] = [
    {
        "key": "browser_automation_dependency_missing",
        "regex": r"npm.*(not found|missing|ENOENT|MODULE_NOT_FOUND|Cannot find module|install failed)|"
        r"(node|npx).*(not found|missing|ENOENT)|"
        r"(playwright|browser[-_ ]?automation|managed[-_ ]?web).*(npm|node|npx|dependency|component).*(missing|not found|failed|unavailable)|"
        r"(缺少|缺失|找不到|无法找到).*(npm|node|npx)|npm.*组件.*(缺失|缺少)",
        "type": "浏览器自动化运行环境缺少 npm/node 组件",
        "direct": "浏览器自动化依赖的 npm/node/npx 组件缺失或安装失败，导致 managed web/browser runtime 无法启动。",
        "indirect": "Agent 侧后续常表现为 managed_web_runtime_unreachable、web capability 探测失败、search/extract/browser 能力不可用；这些是结果，不是最底层根因。",
        "impact": "浏览器自动化、web search/extract 或依赖 managed web runtime 的工具不可用；主模型和 Gateway api_server 可能仍正常。",
        "fix": "修复或重新安装 npm/node/npx 组件与浏览器自动化依赖后重启客户端；同时检查桌面 main.log/bootstrap JSONL 中的安装阶段错误。",
    },
    {
        "key": "managed_web_runtime_unreachable",
        "regex": r"managed_web_runtime_unreachable|clawworker_managed\.web\.capabilities\.failed|web\.capabilities\.failed",
        "type": "托管 Web Runtime 不可达",
        "direct": "Agent 探测 managed web runtime 能力失败，web/browser 能力被标记为不可用。",
        "indirect": "这通常是上层症状；继续检查 desktop main.log、error-stack.jsonl、bootstrap.task.failed、npm/node/npx/playwright 安装日志，确认是否存在运行环境组件缺失。",
        "impact": "浏览器自动化、web search/extract 等托管 Web 能力不可用；Gateway api_server 和模型链路不一定异常。",
        "fix": "先追查 managed web runtime 的启动/安装日志；若发现 npm/node/npx 缺失，按运行环境依赖缺失处理，而不是把问题归为 Gateway 整体故障。",
    },
    {
        "key": "content_policy_violation",
        "regex": r"content[_ ]policy|Content policy violation|invalid_request_error.*400",
        "type": "模型提供商内容审核拦截",
        "direct": "模型提供商返回不可重试的 HTTP 400 / content_policy_violation，主对话流被拒绝继续生成。",
        "indirect": "触发点可能来自用户输入，也可能来自模型正在生成的回复内容。需要结合该 session 的上游工具结果和模型输出阶段判断，不应只看用户最后一句。",
        "impact": "当前 turn 失败；Gateway 和桌面端通常仍可继续处理后续消息。",
        "fix": "重试时缩小敏感上下文、避免让模型复述高风险内容，或切换安全策略不同的 provider/model。",
    },
    {
        "key": "browser_cdp_unavailable",
        "regex": r"CDP endpoint|/json/version: 502|DevTools|browser.*timeout|Chrome.*exit",
        "type": "浏览器/CDP 连接不可用",
        "direct": "browser tool 无法连上 Chrome DevTools Protocol 端口，因此页面打开、点击或截图类工具调用失败。",
        "indirect": "常见原因是浏览器进程启动慢、已崩溃、端口代理返回 502，或桌面浏览器运行时未准备好。",
        "impact": "只影响 browser 相关工具；模型和 Gateway 不一定异常。",
        "fix": "重启浏览器工具或客户端，等待 browser runtime 完全启动后重试；若反复出现，检查 Chrome/代理/端口占用。",
    },
    {
        "key": "no_inference_provider",
        "regex": r"No inference provider configured|Primary provider auth failed|provider auth failed",
        "type": "推理 Provider 未配置或认证失败",
        "direct": "Agent 未找到可用主模型或辅助模型 provider，模型请求无法创建或只能降级。",
        "indirect": "通常是 API key、provider/model 配置、托管模型授权或环境变量缺失导致。",
        "impact": "主 provider 失败会阻断对话；辅助模型失败通常只影响摘要、压缩或标题生成。",
        "fix": "检查客户端模型设置、托管模型授权和 HERMES_HOME 下的 provider 配置；不要在报告中暴露密钥值。",
    },
    {
        "key": "auxiliary_model_unavailable",
        "regex": r"auxiliary_client|Auxiliary.*no provider|summary.*no provider|title_generator.*Content policy",
        "type": "辅助模型链路异常",
        "direct": "辅助模型请求失败，影响标题、摘要、压缩或记忆维护。",
        "indirect": "主聊天模型可能仍正常；若错误只在 title_generator 或 auxiliary_client 中，不能判定主对话失败。",
        "impact": "通常是降级影响；只有当上下文压缩必需且失败时才会阻断主流程。",
        "fix": "检查辅助 provider 配置；标题生成内容审核失败可记录但通常无需修复主聊天。",
    },
    {
        "key": "session_db_write_failed",
        "regex": r"FOREIGN KEY constraint failed|append_message failed|Session DB",
        "type": "会话数据库写入失败",
        "direct": "Session DB 写入消息或状态时违反约束，导致部分消息可能没有持久化。",
        "indirect": "多发生在并发写入、会话记录顺序异常或上一条消息未落库时。",
        "impact": "可能影响 session_search 和历史回放；不一定代表模型执行失败。",
        "fix": "若偶发且后续成功，记录即可；若跨 session 高频出现，需要检查 state/session DB 写入链路。",
    },
    {
        "key": "url_safety_block",
        "regex": r"Blocked request to private/internal address|url_safety|unsafe url|private/internal",
        "type": "URL 安全策略拦截",
        "direct": "URL 解析到内网、保留地址或被策略禁止的目标，工具请求被主动阻止。",
        "indirect": "DNS、代理、VPN 或目标 URL 本身可能导致公网域名解析成内部地址。",
        "impact": "只影响该 URL 的 fetch/browser 操作；Agent 行为符合安全策略。",
        "fix": "确认 URL 是否应允许访问；如确属公网目标，检查 DNS/代理解析结果。",
    },
    {
        "key": "tcp_timeout",
        "regex": r"os error 10060|timed out|Connection timeout|connect timed out",
        "type": "网络连接超时",
        "direct": "Windows 网络层或上游服务在超时时间内没有建立连接。",
        "indirect": "可能由代理/VPN、防火墙、目标服务不可达或平台网关临时不稳定导致。",
        "impact": "取决于模块：provider 超时会阻断模型请求；平台超时只影响对应 Gateway 平台。",
        "fix": "检查代理/VPN和目标服务可达性；若同一 session 重试成功，按瞬态网络故障记录。",
    },
    {
        "key": "streaming_delivery_failed",
        "regex": r"Streaming failed before delivery|interrupted_during_api_call|chat_completion_stream_request|stream_request_complete|streaming response.*failed",
        "type": "模型流式响应传输中断",
        "direct": "Provider 流式响应或 Agent 到客户端传输阶段中断，回复未完整送达用户。",
        "indirect": "常见原因是 provider 连接不稳定、反向代理超时、用户中断或客户端连接断开。",
        "impact": "当前 turn 的展示失败；后续消息通常可继续。",
        "fix": "对比 OpenAI client closed、Turn ended reason、gui ws closed 三类日志，判断是 provider 侧还是客户端连接侧断开。",
    },
    {
        "key": "gateway_platform_error",
        "regex": r"gateway\..*(ERROR|WARNING)|Socket Mode unhealthy|platform.*failed|reconnecting|transport disconnected|dingtalk_stream|slack_bolt|Lark|WeCom|Weixin|Feishu|Dingtalk|open connection failed|no close frame|ConnectionResetError|_ProactorBasePipeTransport|connection_lost",
        "type": "Gateway 平台连接异常",
        "direct": "某个 Gateway 平台连接失败、断开或进入重连。",
        "indirect": "通常是平台 token、网络代理、平台服务或本地 Gateway 进程状态导致。",
        "impact": "影响对应平台消息收发；api_server connected 时桌面本地对话可能仍正常。",
        "fix": "查看 gateway_state.json 中各平台 state/error_message，再结合 gateway.log 的最后一次连接/失败时间。",
    },
    {
        "key": "runtime_bootstrap_failed",
        "regex": r"bootstrap\.task\.failed|runtime_pack|runtime_policy|ports_busy|hermes_not_installed|start_service.*failed|install.*(npm|node|npx|playwright)",
        "type": "桌面 Runtime 启动/更新失败",
        "direct": "Desktop bootstrap/start_service/update 任务失败，导致 Agent Runtime 未按预期启动或更新。",
        "indirect": "常见原因是端口未释放、运行策略为 external、Runtime Pack 更新冲突、Hermes 未安装，或 browser/web 运行环境依赖安装失败。",
        "impact": "可能阻断客户端对话入口或导致桌面使用旧的外部 Agent。",
        "fix": "先确认 CLAWWORKER_RUNTIME_POLICY、端口占用、gateway_state，以及 npm/node/npx/playwright 安装日志，再决定重启 Agent、关闭残留进程或重装运行环境组件。",
    },
]


@dataclass
class LogEvent:
    ts: str
    level: str
    source: str
    module: str
    message: str
    session: str | None = None

    @property
    def dt(self) -> dt.datetime | None:
        return parse_timestamp(self.ts)


def parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip().replace("T", " ").replace("Z", "")
    for fmt in (
        "%Y-%m-%d %H:%M:%S,%f",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return dt.datetime.strptime(value[:26], fmt)
        except ValueError:
            pass
    return None


def parse_user_time(value: str | None, anchor: dt.datetime | None = None) -> dt.datetime | None:
    if not value:
        return None
    parsed = parse_timestamp(value)
    if parsed:
        return parsed
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value.strip()) and anchor:
        bits = [int(x) for x in value.strip().split(":")]
        second = bits[2] if len(bits) > 2 else 0
        return anchor.replace(hour=bits[0], minute=bits[1], second=second, microsecond=0)
    return None


def tail_text(path: Path, max_bytes: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()
        data = fh.read()
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def sanitize_message(message: str, max_len: int = 240) -> str:
    text = message.replace("\r", "\\r").replace("\n", "\\n")
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token|cookie|secret|password)(=|:)\s*['\"]?[^'\"\s,}]+", r"\1\2 [REDACTED]", text)
    text = re.sub(r"(?i)(sk|pk|ak)-[a-z0-9_\-]{12,}", "[REDACTED_KEY]", text)
    text = re.sub(r"(?i)Bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"msg='([^']*)'", lambda m: f"msg=[omitted chars={len(m.group(1))}]", text)
    text = re.sub(r'"(?:prompt|response|content|messages)"\s*:\s*"([^"]{30,})"', lambda m: m.group(0).split(":", 1)[0] + f': "[omitted chars={len(m.group(1))}]"', text)
    text = re.sub(r"'(?:prompt|response|content|messages)'\s*:\s*'([^']{30,})'", lambda m: m.group(0).split(":", 1)[0] + f": '[omitted chars={len(m.group(1))}]'", text)
    if len(text) > max_len:
        return text[: max_len - 24] + f"... [truncated chars={len(text) - max_len + 24}]"
    return text


def parse_log_file(path: Path, source: str, max_bytes: int) -> list[LogEvent]:
    events: list[LogEvent] = []
    current: LogEvent | None = None
    for raw in tail_text(path, max_bytes).splitlines():
        line = raw.rstrip("\n")
        match = LOG_RE.match(line)
        if match:
            session = match.group("session")
            current = LogEvent(
                ts=match.group("ts"),
                level=match.group("level"),
                source=source,
                module=match.group("module"),
                message=sanitize_message(match.group("message")),
                session=session[1:-1] if session else None,
            )
            events.append(current)
            continue
        match = DESKTOP_RE.match(line)
        if match:
            module = "desktop"
            message = match.group("message")
            try:
                payload = json.loads(message)
                module = str(payload.get("event") or payload.get("area") or module)
                message = payload.get("errorMessage") or payload.get("logMessage") or payload.get("progressMessage") or message
                if payload.get("errorCode"):
                    message = f"{payload.get('errorCode')}: {message}"
            except Exception:
                pass
            current = LogEvent(
                ts=match.group("ts").replace(".", ","),
                level=match.group("level").upper(),
                source=source,
                module=module,
                message=sanitize_message(str(message)),
            )
            events.append(current)
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                ts = str(payload.get("ts") or payload.get("timestamp") or "")
                level = str(payload.get("level") or "INFO").upper()
                module = str(payload.get("event") or payload.get("area") or source)
                message = (
                    payload.get("errorMessage")
                    or payload.get("message")
                    or payload.get("logMessage")
                    or payload.get("progressMessage")
                    or json.dumps(payload, ensure_ascii=False)
                )
                if payload.get("errorCode"):
                    message = f"{payload.get('errorCode')}: {message}"
                parsed = parse_timestamp(ts)
                if parsed:
                    ts = parsed.strftime("%Y-%m-%d %H:%M:%S,%f")[:23]
                current = LogEvent(
                    ts=ts,
                    level=level,
                    source=source,
                    module=module,
                    message=sanitize_message(str(message)),
                )
                events.append(current)
                continue
        if current and line.strip():
            current.message = sanitize_message(current.message + " | " + line.strip())
    return events


def default_hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "hermes"
        if candidate.exists():
            return candidate
    return Path.home() / ".hermes"


def desktop_user_data_dirs() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        for name in ("clawworker", "Laiye Worker", "Claw Worker"):
            candidates.append(Path(appdata) / name)
    if platform.system() == "Darwin":
        candidates.extend(
            [
                Path.home() / "Library" / "Application Support" / "clawworker",
                Path.home() / "Library" / "Application Support" / "Laiye Worker",
                Path.home() / "Library" / "Application Support" / "Claw Worker",
            ]
        )
    return unique_paths(candidates)


def desktop_log_files() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for user_data in desktop_user_data_dirs():
        candidates.extend(
            [
                (user_data / "logs" / "main.log", "desktop-main.log"),
                (user_data / "logs" / "main.old.log", "desktop-main.old.log"),
            ]
        )
    if platform.system() == "Darwin":
        log_dir = Path.home() / "Library" / "Logs" / "clawworker"
        candidates.extend(
            [
                (log_dir / "main.log", "desktop-main.log"),
                (log_dir / "main.old.log", "desktop-main.old.log"),
                (log_dir / "error-stack.jsonl", "desktop-error-stack.jsonl"),
                (log_dir / "perf-sample.jsonl", "desktop-perf-sample.jsonl"),
                (log_dir / "ui-trace.jsonl", "desktop-ui-trace.jsonl"),
            ]
        )
    seen: set[Path] = set()
    result: list[tuple[Path, str]] = []
    for path, source in candidates:
        key = path.expanduser()
        if key in seen:
            continue
        seen.add(key)
        result.append((path, source))
    return result


def unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        expanded = path.expanduser()
        if expanded not in seen:
            seen.add(expanded)
            result.append(expanded)
    return result


def collect_events(home: Path, max_bytes: int) -> list[LogEvent]:
    logs = home / "logs"
    files = [
        (logs / "errors.log", "errors.log"),
        (logs / "agent.log", "agent.log"),
        (logs / "gateway.log", "gateway.log"),
        (logs / "gui.log", "gui.log"),
        (logs / "main.log", "desktop-main.log"),
        (logs / "main.old.log", "desktop-main.old.log"),
        (logs / "error-stack.jsonl", "desktop-error-stack.jsonl"),
        (logs / "perf-sample.jsonl", "desktop-perf-sample.jsonl"),
        (logs / "ui-trace.jsonl", "desktop-ui-trace.jsonl"),
        (logs / "codex-gateway-stderr.log", "codex-gateway-stderr.log"),
        (logs / "codex-gateway-stdout.log", "codex-gateway-stdout.log"),
    ]
    files.extend(desktop_log_files())
    events: list[LogEvent] = []
    for path, source in files:
        events.extend(parse_log_file(path, source, max_bytes))
    events.sort(key=lambda e: e.dt or dt.datetime.min)
    return events


def run_cmd(args: list[str], timeout: float = 2.0) -> str | None:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def powershell_file_version(path: Path) -> dict[str, str] | None:
    if os.name != "nt" or not path.exists():
        return None
    escaped = str(path).replace("'", "''")
    script = (
        "$v=(Get-Item -LiteralPath '" + escaped + "').VersionInfo; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "ConvertTo-Json @{FileVersion=$v.FileVersion; ProductVersion=$v.ProductVersion; ProductName=$v.ProductName; CompanyName=$v.CompanyName} -Compress"
    )
    raw = run_cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=4.0)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception:
        return None


def mac_app_version(path: Path) -> dict[str, str] | None:
    info_plist = path / "Contents" / "Info.plist"
    if not info_plist.exists():
        return None
    try:
        with info_plist.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return None
    info: dict[str, str] = {
        "path": str(path),
        "mtime": format_mtime(path),
    }
    mapping = {
        "CFBundleShortVersionString": "FileVersion",
        "CFBundleVersion": "ProductVersion",
        "CFBundleName": "ProductName",
        "CFBundleIdentifier": "BundleIdentifier",
    }
    for src, dst in mapping.items():
        value = data.get(src)
        if value:
            info[dst] = str(value)
    return info


def collect_desktop_version() -> dict[str, str]:
    if platform.system() == "Darwin":
        candidates = [
            Path("/Applications/Laiye Worker.app"),
            Path("/Applications/Claw Worker.app"),
            Path.home() / "Applications" / "Laiye Worker.app",
            Path.home() / "Applications" / "Claw Worker.app",
        ]
        for app in candidates:
            if app.exists():
                info = mac_app_version(app)
                if info:
                    return info
        return {"status": "未找到 Laiye Worker.app / Claw Worker.app"}

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = [Path(os.environ.get("ProgramFiles", "")), Path(os.environ.get("ProgramFiles(x86)", ""))]
    candidates = [
        local / "Programs" / "Claw Worker" / "Laiye Worker" / "Laiye Worker.exe",
        local / "Programs" / "Laiye Worker" / "Laiye Worker.exe",
        local / "Programs" / "Claw Worker" / "Claw Worker.exe",
        local / "Programs" / "clawworker" / "clawworker.exe",
    ]
    for root in program_files:
        candidates.append(root / "Laiye Worker" / "Laiye Worker.exe")
        candidates.append(root / "Claw Worker" / "Laiye Worker.exe")
    for exe in candidates:
        if exe.exists():
            info = powershell_file_version(exe) or {}
            info["path"] = str(exe)
            info["mtime"] = format_mtime(exe)
            return info
    return {"status": "未找到安装目录中的 Laiye Worker.exe"}


def read_text(path: Path, max_chars: int = 10000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def collect_agent_version(home: Path) -> dict[str, str]:
    agent_root = home / "hermes-agent"
    info: dict[str, str] = {"path": str(agent_root)}
    init_py = read_text(agent_root / "hermes_cli" / "__init__.py", 4000)
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", init_py)
    if match:
        info["version"] = match.group(1)
    match = re.search(r"__release_date__\s*=\s*['\"]([^'\"]+)['\"]", init_py)
    if match:
        info["release_date"] = match.group(1)
    if "version" not in info:
        pyproject = read_text(agent_root / "pyproject.toml", 4000)
        match = re.search(r"^version\s*=\s*['\"]([^'\"]+)['\"]", pyproject, re.M)
        if match:
            info["version"] = match.group(1)
    git_dir = agent_root / ".git"
    head = read_text(git_dir / "HEAD", 1000).strip()
    if head:
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            commit = read_text(git_dir / ref, 200).strip()
            info["git_head_ref"] = ref
            if commit:
                info["git_head_commit"] = commit
        else:
            info["git_head_commit"] = head
    main_commit = read_text(git_dir / "refs" / "heads" / "main", 200).strip()
    if main_commit:
        info["git_main_commit"] = main_commit
    return info


def collect_runtime_version(home: Path, gateway_meta: dict[str, Any]) -> dict[str, str]:
    versions_dir = home / "runtimes" / "versions"
    info: dict[str, str] = {"versions_dir": str(versions_dir)}
    argv = gateway_meta.get("argv")
    if isinstance(argv, list):
        joined = " ".join(str(x) for x in argv)
        match = re.search(r"runtimes[\\/]+versions[\\/]+([^\\/]+)", joined)
        if match:
            info["active_from_gateway"] = match.group(1)
    if versions_dir.exists():
        dirs = [p for p in versions_dir.iterdir() if p.is_dir()]
        if dirs:
            latest = max(dirs, key=lambda p: p.stat().st_mtime)
            info["latest_dir"] = latest.name
            info["latest_mtime"] = format_mtime(latest)
    return info


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(read_text(path, 20000))
    except Exception:
        return {}


def is_pid_alive(pid: Any) -> str:
    try:
        pid_int = int(pid)
    except Exception:
        return "unknown"
    if pid_int <= 0:
        return "false"
    if os.name == "nt":
        out = run_cmd(["tasklist", "/FI", f"PID eq {pid_int}", "/FO", "CSV", "/NH"], timeout=3.0)
        if not out:
            return "unknown"
        return "true" if str(pid_int) in out else "false"
    try:
        os.kill(pid_int, 0)
        return "true"
    except ProcessLookupError:
        return "false"
    except PermissionError:
        return "true"
    except Exception:
        return "unknown"


def collect_gateway_status(home: Path) -> dict[str, Any]:
    state = load_json(home / "gateway_state.json")
    pid_meta = load_json(home / "gateway.pid")
    pid = state.get("pid") or pid_meta.get("pid")
    return {
        "state": state.get("gateway_state") or "unknown",
        "pid": pid,
        "pid_alive": is_pid_alive(pid),
        "updated_at": state.get("updated_at"),
        "active_agents": state.get("active_agents"),
        "platforms": state.get("platforms") or {},
        "exit_reason": state.get("exit_reason"),
        "restart_requested": state.get("restart_requested"),
        "argv": state.get("argv") or pid_meta.get("argv"),
    }


def collect_versions(home: Path, gateway_status: dict[str, Any]) -> dict[str, Any]:
    versions = {
        "desktop": collect_desktop_version(),
        "agent": collect_agent_version(home),
        "runtime": collect_runtime_version(home, gateway_status),
        "environment": {
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "source_note": "技能预期在问题发生电脑上运行；这里通常就是目标机器当前环境。若用 --home 分析拷贝日志或临时目录，则仅供参考。",
        },
    }
    node = run_cmd(["node", "--version"], timeout=2.0)
    if node:
        versions["environment"]["node"] = node
    npm = run_cmd(["npm", "--version"], timeout=2.0)
    if npm:
        versions["environment"]["npm"] = npm
    npx = run_cmd(["npx", "--version"], timeout=2.0)
    if npx:
        versions["environment"]["npx"] = npx
    return versions


def choose_window(events: list[LogEvent], since: str | None, until: str | None, minutes: int) -> tuple[dt.datetime | None, dt.datetime | None]:
    timestamps = [e.dt for e in events if e.dt]
    latest = max(timestamps) if timestamps else dt.datetime.now()
    start = parse_user_time(since, latest)
    end = parse_user_time(until, latest)
    if not start:
        start = latest - dt.timedelta(minutes=minutes)
    if not end:
        end = latest
    if start and end and start > end:
        start, end = end, start
    return start, end


def in_window(event: LogEvent, start: dt.datetime | None, end: dt.datetime | None) -> bool:
    ts = event.dt
    if not ts:
        return False
    if start and ts < start:
        return False
    if end and ts > end:
        return False
    return True


def normalize_session(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def choose_session(events: list[LogEvent], requested: str | None) -> str | None:
    if requested:
        return normalize_session(requested)
    candidates: list[tuple[dt.datetime, str]] = []
    for event in events:
        if event.level in {"ERROR", "WARNING"}:
            session = event.session or extract_session(event.message)
            if session and event.dt:
                candidates.append((event.dt, session))
    if not candidates:
        for event in events:
            session = event.session or extract_session(event.message)
            if session and event.dt:
                candidates.append((event.dt, session))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def extract_session(message: str) -> str | None:
    match = SESSION_RE.search(message)
    return match.group(0) if match else None


def event_belongs_to_session(event: LogEvent, session: str | None) -> bool:
    if not session:
        return False
    return event.session == session or session in event.message


def classify_event(event: LogEvent) -> dict[str, str] | None:
    haystack = f"{event.module} {event.message}"
    for pattern in ERROR_PATTERNS:
        if re.search(pattern["regex"], haystack, re.I):
            return pattern
    if event.level == "ERROR":
        return {
            "key": "unknown_error",
            "type": "未归类错误",
            "direct": "日志中出现 ERROR，但当前规则无法自动映射到已知错误模式。",
            "indirect": "需要结合前后工具调用、模型请求和 Gateway 状态人工判断。",
            "impact": "影响范围需继续确认。",
            "fix": "围绕该 session 读取更宽时间窗口，并检查同类错误是否跨 session 重复。",
        }
    return None


def extract_model_info(events: list[LogEvent]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    requests: list[dict[str, str]] = []
    for event in events:
        provider = first(PROVIDER_RE, event.message)
        model = first(MODEL_RE, event.message)
        platform_name = first(PLATFORM_RE, event.message)
        base_url = first(BASE_URL_RE, event.message)
        reason = first(TURN_REASON_RE, event.message)
        if provider or model or platform_name or base_url or reason:
            row: dict[str, str] = {
                "time": event.ts,
                "source": event.source,
                "module": event.module,
            }
            if provider:
                row["provider"] = provider
                info["provider"] = provider
            if model:
                row["model"] = model
                info["model"] = model
            if platform_name:
                row["platform"] = platform_name
                info["platform"] = platform_name
            if base_url:
                row["base_url_host"] = url_host(base_url)
                info["base_url_host"] = url_host(base_url)
            if reason:
                row["turn_end_reason"] = reason
                info["last_turn_end_reason"] = reason
            requests.append(row)
    info["recent_requests"] = requests[-8:]
    return info


def first(regex: re.Pattern[str], text: str) -> str | None:
    match = regex.search(text)
    return match.group(1) if match else None


def url_host(value: str) -> str:
    parsed = urlparse(value)
    if parsed.netloc:
        return parsed.netloc
    return value.split("/", 1)[0]


def top_errors(events: list[LogEvent]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in events:
        if event.level not in {"ERROR", "WARNING", "CRITICAL"}:
            continue
        classification = classify_event(event)
        if not classification and event.level == "WARNING":
            continue
        dedup_key = (event.ts, event.module, event.message, (classification or {}).get("key", "warning"))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        item = asdict(event)
        item["classification"] = classification or {
            "key": "warning",
            "type": "警告",
            "direct": "日志记录了可恢复警告。",
            "indirect": "需要结合是否重复和后续是否成功判断影响。",
            "impact": "未知",
            "fix": "若后续同类操作成功，按瞬态问题记录。",
        }
        errors.append(item)
    return errors


def root_cause_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    key = item["classification"]["key"]
    priority = {
        "browser_automation_dependency_missing": 0,
        "runtime_bootstrap_failed": 1,
        "managed_web_runtime_unreachable": 2,
        "browser_cdp_unavailable": 3,
        "no_inference_provider": 4,
        "content_policy_violation": 5,
        "streaming_delivery_failed": 6,
        "tcp_timeout": 7,
        "gateway_platform_error": 8,
        "auxiliary_model_unavailable": 9,
        "session_db_write_failed": 10,
        "url_safety_block": 11,
        "unknown_error": 50,
    }.get(key, 40)
    level_rank = 0 if item["level"] in {"ERROR", "CRITICAL"} else 1
    return (priority, level_rank, item["ts"])


def summarize_root_cause(error_items: list[dict[str, Any]], gateway_status: dict[str, Any], model_info: dict[str, Any]) -> dict[str, str]:
    if not error_items:
        return {
            "type": "未发现明确 ERROR/WARNING",
            "confidence": "低",
            "direct": "所选窗口内没有足够的错误日志证据。",
            "indirect": "问题可能发生在日志窗口之外、session 持久化之前，或只表现为前端/UI 状态异常。",
            "impact": "无法自动判断。",
            "fix": "扩大时间窗口，检查 desktop-main.log 和用户描述中的精确时间点。",
        }
    ranked = sorted(error_items, key=root_cause_priority)
    primary = ranked[0]["classification"]
    repeat_count = sum(1 for item in error_items if item["classification"]["key"] == primary["key"])
    confidence = "高" if ranked[0]["level"] == "ERROR" or repeat_count >= 3 else "中"
    direct = primary["direct"]
    indirect = primary["indirect"]
    if gateway_status.get("state") == "running" and gateway_status.get("pid_alive") == "true":
        indirect += " 当前 gateway_state 显示 Gateway 进程仍在运行，因此除非错误模块指向 gateway/platform，否则不应把根因直接归为 Gateway 整体崩溃。"
    if model_info.get("provider") or model_info.get("model"):
        indirect += f" 最近模型链路显示 provider={model_info.get('provider', 'unknown')}、model={model_info.get('model', 'unknown')}，需要区分 provider 返回错误和本地工具错误。"
    return {
        "type": primary["type"],
        "confidence": confidence,
        "direct": direct,
        "indirect": indirect,
        "impact": primary["impact"],
        "fix": primary["fix"],
        "repeat_count": str(repeat_count),
    }


def format_mtime(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


def fmt(value: Any) -> str:
    if value is None or value == "":
        return "未找到"
    return str(value)


def md_escape(value: Any) -> str:
    return fmt(value).replace("|", "\\|").replace("\n", " ")


def compact_commit(value: str | None) -> str:
    if not value:
        return "未找到"
    return f"{value[:12]} ({value})" if len(value) > 12 else value


def render_markdown(data: dict[str, Any]) -> str:
    versions = data["versions"]
    gateway = data["gateway_status"]
    model = data["model_info"]
    root = data["root_cause"]
    session = data.get("session") or "未定位到 session"
    start, end = data.get("window_start"), data.get("window_end")
    errors = data["errors"]
    timeline = data["timeline"]

    desktop = versions["desktop"]
    agent = versions["agent"]
    runtime = versions["runtime"]
    env = versions["environment"]
    platforms = gateway.get("platforms") or {}
    platform_summary = ", ".join(
        f"{name}={info.get('state', 'unknown')}" for name, info in sorted(platforms.items())
    ) or "未找到"

    lines: list[str] = []
    lines.append("# 客户端问题诊断报告")
    lines.append("")
    lines.append("## 结论")
    lines.append(f"- 初步根因：{root['type']}")
    lines.append(f"- 置信度：{root['confidence']}")
    lines.append(f"- 直接原因：{root['direct']}")
    lines.append(f"- 深层原因分析：{root['indirect']}")
    lines.append(f"- 影响范围：{root['impact']}")
    lines.append(f"- 建议处理：{root['fix']}")
    if root.get("repeat_count"):
        lines.append(f"- 同类错误出现次数：{root['repeat_count']}")
    lines.append("")
    lines.append("## 版本信息")
    lines.append(f"- 客户端版本：{fmt(desktop.get('FileVersion') or desktop.get('ProductVersion'))}")
    lines.append(f"- 客户端产品：{fmt(desktop.get('ProductName'))}")
    lines.append(f"- 客户端安装路径：{fmt(desktop.get('path'))}")
    lines.append(f"- Agent 版本：{fmt(agent.get('version'))} {fmt(agent.get('release_date'))}")
    lines.append(f"- Agent Git HEAD：{compact_commit(agent.get('git_head_commit'))}")
    lines.append(f"- Agent Git main：{compact_commit(agent.get('git_main_commit'))}")
    lines.append(f"- Runtime 版本目录：active={fmt(runtime.get('active_from_gateway'))}, latest={fmt(runtime.get('latest_dir'))}")
    lines.append(f"- Python：{fmt(env.get('python'))}")
    lines.append(f"- Node.js：{fmt(env.get('node'))}")
    lines.append(f"- npm：{fmt(env.get('npm'))}")
    lines.append(f"- npx：{fmt(env.get('npx'))}")
    lines.append(f"- OS：{fmt(env.get('os'))}")
    lines.append(f"- 环境版本来源：{fmt(env.get('source_note'))}")
    lines.append("")
    lines.append("## Gateway 状态")
    lines.append(f"- 状态：{fmt(gateway.get('state'))}")
    lines.append(f"- PID：{fmt(gateway.get('pid'))}，进程存活：{fmt(gateway.get('pid_alive'))}")
    lines.append(f"- 更新时间：{fmt(gateway.get('updated_at'))}")
    lines.append(f"- active_agents：{fmt(gateway.get('active_agents'))}")
    lines.append(f"- 平台状态：{platform_summary}")
    if gateway.get("exit_reason"):
        lines.append(f"- exit_reason：{fmt(gateway.get('exit_reason'))}")
    lines.append("")
    lines.append("## 模型信息")
    lines.append(f"- Provider：{fmt(model.get('provider'))}")
    lines.append(f"- Model：{fmt(model.get('model'))}")
    lines.append(f"- Platform：{fmt(model.get('platform'))}")
    lines.append(f"- Base URL Host：{fmt(model.get('base_url_host'))}")
    lines.append(f"- 最近 Turn 结束原因：{fmt(model.get('last_turn_end_reason'))}")
    if model.get("scope"):
        lines.append(f"- 证据范围：{fmt(model.get('scope'))}")
    if model.get("recent_requests"):
        lines.append("")
        lines.append("| 时间 | 模块 | Provider | Model | 备注 |")
        lines.append("|---|---|---|---|---|")
        for req in model["recent_requests"][-5:]:
            note = req.get("turn_end_reason") or req.get("base_url_host") or req.get("platform") or ""
            lines.append(
                f"| {md_escape(req.get('time'))} | {md_escape(req.get('module'))} | "
                f"{md_escape(req.get('provider'))} | {md_escape(req.get('model'))} | {md_escape(note)} |"
            )
    lines.append("")
    lines.append("## 问题时间与 Session")
    lines.append(f"- 调查窗口：{fmt(start)} 至 {fmt(end)}")
    lines.append(f"- 关联 Session ID：{session}")
    lines.append(f"- HERMES_HOME：{fmt(data.get('home'))}")
    lines.append("")
    lines.append("## 错误信息")
    if not errors:
        lines.append("- 所选窗口内未发现明确 ERROR/WARNING。")
    else:
        lines.append("| 时间 | 来源 | 级别 | 模块 | 类型 | 错误信息 |")
        lines.append("|---|---|---|---|---|---|")
        for item in errors[:12]:
            lines.append(
                f"| {md_escape(item.get('ts'))} | {md_escape(item.get('source'))} | {md_escape(item.get('level'))} | "
                f"{md_escape(item.get('module'))} | {md_escape(item.get('classification', {}).get('type'))} | "
                f"{md_escape(item.get('message'))} |"
            )
        if len(errors) > 12:
            lines.append(f"- 另有 {len(errors) - 12} 条错误/警告已省略；需要时用 --json 查看结构化摘要。")
    lines.append("")
    lines.append("## 事件链")
    if not timeline:
        lines.append("- 未找到可关联的事件链。")
    else:
        for item in timeline[:40]:
            lines.append(f"- {item['ts']} [{item['level']}] {item['source']} {item['module']}: {item['message']}")
        if len(timeline) > 40:
            lines.append(f"- 另有 {len(timeline) - 40} 条事件已省略。")
    lines.append("")
    lines.append("## 证据边界")
    lines.append("- 本报告只使用本机日志和元数据；已隐藏完整 prompt/response、token、cookie、密钥等敏感内容。")
    lines.append("- 自动根因只作为初步判断；若用户给出更精确时间或可复现步骤，应扩大窗口并复核原始日志。")
    return "\n".join(lines) + "\n"


def build_timeline(events: list[LogEvent], session: str | None) -> list[dict[str, str]]:
    interesting = []
    keywords = re.compile(
        r"conversation turn|Tool .*returned|tool_|OpenAI client|Turn ended|ERROR|WARNING|browser|CDP|gateway|ws accepted|ws closed|bootstrap|managed_web|npm|node|npx|playwright|install",
        re.I,
    )
    for event in events:
        if session and event_belongs_to_session(event, session):
            interesting.append(event)
        elif event.level in {"ERROR", "WARNING"} and keywords.search(f"{event.module} {event.message}"):
            interesting.append(event)
        elif keywords.search(f"{event.module} {event.message}") and event.source in {"gui.log", "desktop-main.log"}:
            interesting.append(event)
    dedup: list[LogEvent] = []
    seen = set()
    for event in interesting:
        key = (event.ts, event.module, event.message)
        if key not in seen:
            seen.add(key)
            dedup.append(event)
    return [asdict(e) for e in dedup]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect sanitized Laiye Worker client diagnostics.")
    parser.add_argument("--home", help="HERMES_HOME path. Defaults to HERMES_HOME, %%LOCALAPPDATA%%\\hermes on Windows, or ~/.hermes.")
    parser.add_argument("--session", help="Session/client/run id to focus on.")
    parser.add_argument("--since", help="Window start, e.g. '2026-06-23 11:40' or '11:40'.")
    parser.add_argument("--until", help="Window end, e.g. '2026-06-23 12:10' or '12:10'.")
    parser.add_argument("--minutes", type=int, default=60, help="Default window size when --since is omitted.")
    parser.add_argument("--max-mb", type=int, default=16, help="Tail size per log file.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--output", help="Write report to a file instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    home = Path(args.home).expanduser() if args.home else default_hermes_home()
    max_bytes = max(1, args.max_mb) * 1024 * 1024
    events = collect_events(home, max_bytes)
    start, end = choose_window(events, args.since, args.until, args.minutes)
    window_events = [event for event in events if in_window(event, start, end)]
    session = choose_session(window_events, args.session)
    if session:
        focused = [event for event in events if event_belongs_to_session(event, session) or in_window(event, start, end)]
    else:
        focused = window_events
    gateway_status = collect_gateway_status(home)
    versions = collect_versions(home, gateway_status)
    model_info = extract_model_info([event for event in focused if event_belongs_to_session(event, session)] or focused)
    if not model_info.get("provider") and not model_info.get("model"):
        fallback_model_info = extract_model_info(events)
        for key, value in fallback_model_info.items():
            model_info.setdefault(key, value)
        if fallback_model_info.get("provider") or fallback_model_info.get("model"):
            model_info["scope"] = "调查窗口内未找到模型请求，已回退到日志中最近可见的模型信息"
    if session:
        error_scope = [
            event
            for event in focused
            if event_belongs_to_session(event, session)
            or (event.level in {"ERROR", "WARNING", "CRITICAL"} and in_window(event, start, end))
        ]
    else:
        error_scope = focused
    errors = top_errors(error_scope)
    root_cause = summarize_root_cause(errors, gateway_status, model_info)
    data = {
        "home": str(home),
        "window_start": start.strftime("%Y-%m-%d %H:%M:%S") if start else None,
        "window_end": end.strftime("%Y-%m-%d %H:%M:%S") if end else None,
        "session": session,
        "versions": versions,
        "gateway_status": gateway_status,
        "model_info": model_info,
        "errors": errors,
        "timeline": build_timeline(focused, session),
        "root_cause": root_cause,
    }
    output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_markdown(data)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
