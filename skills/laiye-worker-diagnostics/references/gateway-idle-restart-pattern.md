# Gateway 空闲恢复重启诊断模式

## 症状

用户反馈客户端启动用了 2-3 分钟，期间 AI 功能不可用。

## 根因模式

LaiyeWorker 桌面端从长时间空闲（屏幕锁定、后台、用户离开）恢复后，会主动用 `signal=UNKNOWN` 替换 Gateway 子进程。这不是崩溃，是桌面端的设计行为——它不信任空闲了数小时的 Gateway 进程。

## 诊断步骤

### 1. 确认触发源

在 agent.log 中搜索 `"node"` UA 的 HTTP 请求，找到桌面端最后一次活跃时间和恢复时间：

```
search_files(pattern='"node"', path="<HERMES_HOME>/logs", file_glob="agent.log")
```

如果两次 "node" 请求之间间隔 > 1 小时，说明桌面端在此期间空闲。

### 2. 确认重启信号

在 gateway.log 中搜索 `Received UNKNOWN`：

```
search_files(pattern="Received UNKNOWN", path="<HERMES_HOME>/logs", file_glob="gateway.log")
```

关键日志行示例：
```
2026-06-24 15:07:58,909 INFO gateway.run: Received UNKNOWN as a planned gateway stop — exiting cleanly
2026-06-24 15:07:58,914 WARNING gateway.run: Shutdown context: signal=UNKNOWN under_systemd=no parent_pid=46616 parent_name=? loadavg_1m=? parent_cmdline='(unknown)'
```

`parent_pid` 是桌面端进程 PID。`parent_cmdline='(unknown)'` 是 Windows 限制，非异常。

### 3. 计算耗时

关闭耗时：从 `Received UNKNOWN` 到 `Gateway stopped` 的时间差（通常 7-13 秒）。
空窗期：从 `Gateway stopped`（旧进程最后一行）到 `Starting Hermes Gateway`（新进程第一行）。桌面端在此期间启动新 Python 进程、加载 42+ 插件。
启动耗时：从 `Starting Hermes Gateway` 到首个平台连接成功。

用户感知总耗时 = 关闭 + 空窗 + 启动，通常 2-3 分钟。

### 4. 确认非崩溃

检查 gateway.log 中是否有 "Previous gateway exited cleanly" 行：
```
2026-06-24 15:10:19,599 INFO gateway.run: Previous gateway exited cleanly — skipping session suspension
```

有此行说明旧进程优雅退出，session 不需要恢复，确认是计划内重启。

### 5. 检查桌面端自身是否重启

搜索 desktop main.log 中当天的 bootstrap 事件：

```
search_files(pattern="2026-06-24.*bootstrap", path="%APPDATA%/clawworker/logs", file_glob="main.log")
```

如果当天没有 bootstrap 事件，说明桌面端进程本身没有重启，只是在运行中替换了 Gateway 子进程。

## 案例时间线（2026-06-24 15:07-15:10）

| 时间 | 事件 |
|------|------|
| 06-23 17:57:01 | 桌面端最后一次健康检查（`GET /health`, UA=node） |
| 06-23 17:57 ~ 06-24 15:07 | 约 21 小时桌面端无任何请求（空闲期） |
| 15:07:14.821 | 桌面端恢复活动：`GET /health` 200 + `GET /v1/models` 200 |
| 15:07:57.270 | 桌面端开始密集探测：`GET /health/detailed`，约每 350ms 一次 |
| 15:07:58.909 | Gateway 收到 UNKNOWN 信号，判定为计划内停止 |
| 15:07:58.917 | Gateway 开始关闭：通知各平台、断开连接 |
| 15:08:12.217 | 旧 Gateway 完全停止（teardown 13.30s） |
| 15:08:12 → 15:10:17 | 空窗期（约 125 秒）：桌面端启动新 Python 进程，42 插件冷加载 |
| 15:10:19.568 | 新 Gateway 开始启动序列 |
| 15:10:20.756 | API server 就绪（http://127.0.0.1:8642） |
| 15:10:22.819 | 首个消息平台恢复（Wecom connected） |
| 15:10:25.109 | 桌面端 UI 恢复请求（`GET /api/jobs`, UA=LaiyeWorker/0.6.5） |

总用户不可用时间：15:07:58 → 15:10:22 = 2 分 24 秒。

## 关键判断

- 所有健康检查返回 200（Gateway 完全正常），桌面端明知健康仍然选择重启 → 这是策略而非故障
- UNKNOWN 信号在 gateway.log 中出现 9 次（6月12日-24日），每次模式相同 → 固定行为
- desktop main.log 没有当天事件 → 桌面端进程未重启，仅替换子进程
- 新 Gateway 启动参数、平台配置与旧进程完全一致 → 非配置变更触发
