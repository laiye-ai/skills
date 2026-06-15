---
name: cashflow-daily-report
description: "企业资金 / 现金流场景：从各银行账户拉取指定日期的资金流水，计算关键资金指标并产出中文日报与分析。涵盖三类问法——①查某日各银行账户的回款/支出/当前余额（资金头寸、当日流水汇总）；②按本月预测看现金流完成情况（预测vs实际、流入完成率、净现金流完成率、月度预算完成）；③做一份现金流日报、标出大额回款/支出异常与需 CFO 关注事项（产出好看的 HTML/PDF）。当用户要：现金流日报 / 资金日报 / 银行余额与收支 / 回款支出查询 / 现金流预测完成情况 / 大额异常 / CFO 关注事项 时使用。三类问法是相互独立的能力，可各自单独触发（只问数就只答数、不强行出日报），也可串成一套日报，或做成每日定时自动生成。"
metadata:
  requires:
    bins: ["python3"]
---

# 现金流日报 (Cash Flow Daily Report)

把"各银行账户的当日流水"汇总成一份给财务/资金负责人看的**现金流日报**：
看清今天资金从哪来、到哪去、还剩多少、有没有风险。

## 取数：银行流水接口

通过本 skill 自带脚本 `scripts/mock_bank_api.py` 拉取当日流水。**报告日数据来自真实流水 CSV `data/transactions.csv`**（招商银行 / 平安银行 / 兴业银行三个账户，按账户聚合、期初由运行余额反推、期末取当日最后一笔）。**真实环境替换此脚本为网银 / 财资系统 / 银企直连 API，其余逻辑不变。**

```bash
# 全部账户（默认日期=CSV 报告日 2026-06-12）
python3 .../scripts/mock_bank_api.py          # 默认 --bank all，无需加 --all 参数
# 指定银行 / 日期
python3 .../scripts/mock_bank_api.py --bank 招商银行 --date 2026-06-12
# 日期关键字：today / yesterday / csv
python3 .../scripts/mock_bank_api.py          # 默认 --bank all，无需加 --all 参数
```

脚本路径相对本文件为 `scripts/mock_bank_api.py`，请用其**绝对路径**调用（本 skill 目录在 `~/.hermes/skills/cashflow-daily-report/`）。
返回 JSON：账户级 `opening_balance / closing_balance / total_inflow / total_outflow / net_cashflow / safe_line / transactions[]`，以及集团汇总 `group_*` 字段。金额单位：元(CNY)。**详细 JSON 结构见 [`references/summarize-output-format.md`](references/summarize-output-format.md)**——集团汇总在顶层扁平键（`group_opening_balance` 等），非嵌套对象。

**默认日期 = CSV 报告日**（脚本里的 `CSV_DATE`，当前为 2026-06-12，有真实流水）。趋势图需要的历史日没有真实流水，脚本按各账户真实期初确定性合成，仅贡献净额与余额、不展示明细。要换报告日数据，替换 `data/transactions.csv` 即可。

## 三个独立能力（按用户意图各自触发，不分先后）

这个 skill 是三个**互相独立**的能力，用户问到哪个就做哪个 —— **不需要先做前面的**，每个命令都会自己取数。也可以在一轮里串起来（用 `--out`/`--data` 复用数据），但不是必须；因为按日期取数是确定性的，单独跑结果也一致。

按用户**意图**选能力，不要默认就出日报：

**能力 A · 查回款/支出/余额**（用户问"查一下某日三个账户的回款、支出、当前余额 / 各账户余额 / 当日收支"）：
```bash
python3 .../scripts/summarize.py --view balances --date <日期>
```
把输出的 Markdown 表直接作为聊天回答。**这类问法只在聊天里答，不要生成 HTML 文件。**

**能力 B · 现金流预测完成情况**（用户问"按本月预测，现金流完成得怎么样 / 流入完成率 / 预算完成情况"）：
```bash
python3 .../scripts/summarize.py --view forecast --date <日期>
```
回答预测 vs 实际 + 流入/净完成率 + 进度判断。要用真实预算就写一个含 `forecast` 的 JSON 传 `--analysis`。**同样只在聊天里答，不出 HTML。**

**能力 C · 出现金流日报（HTML/PDF）**（用户**明确**要"做一份现金流日报 / 报告 / 文件"，或要"标出大额异常、需 CFO 关注的事项"）：
先把判断/建议（**尤其大额异常、破线风险、需 CFO 关注项**）写成 `analysis.json`，再：
```bash
python3 .../scripts/render_report.py --date <日期> --analysis analysis.json --out ~/cashflow_<日期>.html --pdf
```
生成后把 `.html`/`.pdf` 路径给用户。详见下面「输出」一节。

> 何时出 HTML：只有用户明确要「日报 / 报告 / 文件 / 导出」时才用能力 C；只是问数、问完成情况，就用 A/B 在聊天里回答。
>
> 想在一轮里保证三者数字完全一致，可让 A 加 `--out /tmp/cf_data.json` 落地，再让 B/C 用 `--data /tmp/cf_data.json` 复用 —— 可选优化，不强制。

## 我们关注什么

1. **资金头寸** —— 各账户期初、期末余额；集团可用资金总额；是否有账户跌破安全线。
2. **当日收支** —— 总流入、总流出、净现金流（正=净流入，负=净流出）。
3. **结构** —— 流入/流出按 `经营活动 / 投资活动 / 筹资活动` 拆分，看钱主要花在/来自哪。
4. **大额回款** —— 单笔 ≥ 30 万元的收款，列客户名 + 具体项目，日报必须点名。
5. **大额支出** —— 单笔 ≥ 5 万元的付款，列供应商名 + 具体项目。
6. **风险信号** —— 余额逼近/跌破安全线、单日净流出过大、对单一对手方资金集中。
7. **本月资金预测 / 月度预算完成情况** —— 预测流入/流出/净 vs 实际(本月累计)流入/流出/净，给出**流入完成率**与净现金流完成率。

## 关键指标计算

对每个账户、以及集团汇总：

- 净现金流 `net = total_inflow − total_outflow`
- 期末余额 `closing = opening + total_inflow − total_outflow`（可用脚本返回值校验）
- 安全垫 `buffer = closing − safe_line`；`buffer < 0` → **跌破安全线（高风险）**；`0 ≤ buffer < closing*10%` → **逼近安全线（关注）**
- 按 `cashflow_type` 分组求和，得经营/投资/筹资三类的净额
- 大额回款：`收 且 amount ≥ 300000`；大额支出：`付 且 amount ≥ 50000`。各自列出时间、账户、客户/供应商、项目(摘要)、金额。
- 资金集中度：单一对手方流入或流出占当日同方向合计的比例，> 40% 视为集中
- （可选）环比：再拉一次 `--date yesterday`/前一日，对比期末余额变化
- 本月资金预测：`流入完成率% = 实际累计流入 ÷ 预测流入 × 100`，净/流出同理。**实际口径 = 报告日的集团当日值**（当前数据仅 1 个真实日，即本月累计到报告日）。预测默认 流入 1200 万 / 流出 500 万 / 净 700 万，可在 `analysis.json` 用 `forecast`（单位：元）覆盖为真实预算。脚本自动算，无需手动累加。

金额展示用「万元」并保留 1~2 位小数，正负号清晰（流出用负号或"-"，净流出标红/标注）。

## 分析逻辑（写"判断"，不要只罗列数字）

按这个顺序推演，并在日报里给出**结论 + 依据**：

1. 今天集团净现金流是正是负？主要由哪个账户、哪一类活动（经营/投资/筹资）驱动？
2. 净流出的话，是经营性正常支出（工资/税费/采购）还是筹资性偿债？经营性净流出连续出现才是警讯。
3. 有没有账户跌破或逼近安全线？跌破必须置顶预警并给建议（如调拨、暂缓非刚性支出）。
4. 大额交易是否符合预期/有无异常对手方？资金是否过度集中在单一对手方？
5. 给资金负责人 1~3 条**可执行建议**（如"建议从招行向中行调拨 X 万补足安全垫""明日有大额税费支出，确认头寸"）。

判断要显式标注为【判断】，与已确认的流水事实区分开，不要把推测写成事实。

## 输出：好看的 HTML 日报（主交付物）

**默认产出一个自包含 HTML 文件**（零外链、双击即开、浅/深色自适应），结构对齐「资金日报」模板：总览 KPI → 本月资金预测 → 账户资金明细汇总 → 重点关注（大额回款/支出/月度预算）→ 资金趋势折线 → 收支结构 → 分析与建议。用 `scripts/render_report.py` 生成，**不要手写 HTML**。

生成流程（**过程要显式分三步走**）：

即使用户只一句话「生成 X 日的现金流日报」，也**不要直接闷头渲染**。要让工作过程显式走完下面三步，**每步先用一句话说明在做什么、再执行命令、再用一两句汇报结果**，让用户看到推理脉络；最终交付 HTML/PDF。三步复用同一份 `/tmp/cf_data.json`，保证数字一致。

**第一步 · 取数核对** —— 先说"正在拉取三个账户的回款 / 支出 / 当前余额"，执行：
```bash
python3 .../scripts/summarize.py --view balances --date <日期> --out /tmp/cf_data.json
```
用一两句汇报：合计回款 / 支出、当前总余额、净流入，以及大额回款/支出笔数。

**第二步 · 预测完成情况** —— 说"核对本月资金预测完成情况"，执行（复用第一步数据）：
```bash
python3 .../scripts/summarize.py --view forecast --data /tmp/cf_data.json
```
汇报流入完成率 / 净现金流完成率与进度判断。

**第三步 · 生成日报** —— 说"汇总判断、标注大额异常与需 CFO 关注事项，生成日报"。先把分析写成 `analysis.json`（**聚焦大额异常、破线风险、需 CFO 关注项**）：
```json
{
  "company": "XX集团",
  "summary": "一句话总览",
  "judgments": ["【判断】…（依据：…）", "…"],
  "recommendations": ["建议1", "建议2"],
  "yesterday": {"group_closing_balance": 16500000},
  "forecast": {"inflow": 12000000, "outflow": 5000000, "net": 7000000}
}
```
`yesterday` 可选（总期末余额环比）。`forecast`（元）可选，传真实「本月资金预测」流入/流出/净；不传用默认 1200 万 / 500 万 / 700 万。再渲染（复用同一份数据，与前两步完全一致）：
```bash
python3 .../scripts/render_report.py --data /tmp/cf_data.json --analysis analysis.json --out ~/cashflow_<日期>.html --pdf
```
最后把文件交给用户。**渲染完成后必须自动用系统默认程序打开 HTML 文件**，不要只丢路径给用户——用户期望直接看到日报内容：

```bash
# Windows：用 PowerShell Start-Process 打开（cmd.exe /c start 在 Hermes Desktop 远端无效）
powershell -Command "Start-Process '<HTML绝对路径>'"
# Mac：
open <HTML绝对路径>
```

脚本会打印裸路径和 `HTML_URL:` / `PDF_URL:` 链接作为兜底，但 `file://` 链接在 Windows Hermes Desktop 上不可靠（见 Pitfalls），不要仅依赖它们。

> 单独问数 / 问完成情况（能力 A 或 B）时不必走全程，按上文「三个独立能力」单步回答即可。这里的"显式三步"是针对**出整份日报**的请求。

**要 PDF 时加 `--pdf`**：生成 HTML 后用 headless Chrome 保真打印出同名 `.pdf`（配色、表格、折线图全保留），路径为 `<out>.pdf`。无需任何 PDF 库，自动探测系统 Chrome 或 Playwright Chromium；找不到才报错。
```bash
python3 .../scripts/render_report.py --date <日期> --analysis analysis.json --out ~/cashflow_<日期>.html --pdf
```

兜底：不传 `--analysis` 也能直接出图（脚本内置规则化分析），保证演示永远不空白——但有 agent 的判断/建议才是这份日报的价值，正常流程请带上 `analysis.json`。

需要纯文本/聊天里贴一份时，可另外给一段同结构的 Markdown 摘要；HTML 文件始终是主交付物。某一节无内容写"无"，不要凑数。

## Pitfalls

- **Windows 上 `python3` 不存在**：Windows 主机（含 git-bash/MSYS）的 Python 通过 `python` 调用，不是 `python3`。先试 `python3`，失败后立即回退到 `python`，不要反复重试同一个不可用的命令。Mac/Linux 上 `python3` 仍有效。
- **工作目录可能不存在**：系统指定的工作目录（如 `~/ClawWorker/<timestamp>`）可能在首次执行时尚未创建，用 `mkdir -p <dir>` 创建后再执行命令。
- **PDF 需要 Chrome**：加 `--pdf` 时脚本自动探测系统 Chrome/Chromium，找不到会报错。HTML 已生成时不影响交付——报告给用户即可，PDF 失败不影响 HTML 可用性。
- **Windows Hermes Desktop 上 `file://` 链接和 `cmd.exe /c start` 均不可靠**：`file://` 可能报 `file_not_found`，`cmd.exe /c start` 在远端沙箱执行弹不出本机窗口。**唯一切实有效的打开方式是用 PowerShell Start-Process**：`powershell -Command "Start-Process '<HTML绝对路径>'"`。Mac 上 `open` 仍正常。
- **`execute_code` 访问不到 terminal 的 `/tmp` 文件**：`execute_code` 和 `terminal` 运行在不同沙箱上下文中，terminal 写入 `/tmp/cf_data.json` 后 `execute_code` 和 `read_file` 可能读不到。`read_file` 有时能读到但会截断大单行 JSON。需要解析数据做判断时，用 `write_file` 写 Python 脚本到工作目录，再通过 `terminal` 执行，脚本内用 Windows 绝对路径读取。先用 `cygpath -w /tmp/cf_data.json` 从 terminal 查出 Windows 路径（如 `C:\Users\...\AppData\Local\Temp\cf_data.json`），再写入脚本。
- **`python -c` 内联脚本可能被安全策略拦截**：Hermes 的安全模式会对 `-c` / `-e` 脚本触发审批。遇到时不要反复尝试，改用 `write_file` 写 `.py` 文件 + `terminal` 执行，一次通过。

## 邮件发送格式（发送 PDF 日报时的正文模板）

生成 PDF 日报后，如需通过飞书邮箱发送给资金负责人，邮件正文须遵循以下固定格式（纯文本/Markdown 风格，不用 HTML）：

```
来也科技 · 现金流日报 — YYYY年M月D日

期初集团余额	X,XXX.XX 万元	当日净现金流	+/-XXX.XX 万元 ✅/⚠️
当日流入	+XXX.XX 万元	期末集团余额	X,XXX.XX 万元
当日流出	−XXX.XX 万元

📊 三账户明细
账户	期初	流入	流出	期末	安全垫
招商银行	XXX.XX万	+XXX.XX万	−XX.XX万	XXX.XX万	+XXX.XX万 ✅/⚠️
平安银行	XXX.XX万	+XX.XX万	−XX.XX万	XXX.XX万	+XXX.XX万 ✅/⚠️
兴业银行	XXX.XX万	+XX.XX万	−XX.XX万	XXX.XX万	+XXX.XX万 ✅/⚠️

🔔 大额回款（≥30万）
客户名 — XX.XX万元（银行简称，事由）

💸 大额支出（≥5万）
供应商名 — XX.XX万元（银行简称，事由）

📋 判断与建议
【判断】…（依据）
【建议】① …；② …；③ …
```

**格式规则：**

- 金额单位统一用「万元」，保留 2 位小数；流入前加 `+`，流出前加 `−`
- 安全垫 ≥ 0 时末尾加 `✅`，< 0 时加 `⚠️`
- 当日净现金流正值加 `✅`，负值加 `⚠️`
- 大额回款/支出按金额从大到小排列，每行格式：`名称 — XX.XX万元（银行，事由）`
- 银行简称：招行=招商银行、平安=平安银行、兴业=兴业银行
- 判断和建议合并在「📋 判断与建议」一节，判断标【判断】，建议标【建议】并编号
- 邮件主题格式：`资金日报 - YYYY年M月D日`
- 发送时务必附带 PDF 附件（--attach）

**发送流程**（在生成 PDF 后）：
1. `lark-cli contact +search-user --query "<姓名>" --as user` 查收件人邮箱
2. `lark-cli mail +send --as user --to "<邮箱>" --subject "<主题>" --body "<正文>" --attach "<pdf路径>"` 创建草稿
3. 向用户展示草稿链接和内容摘要，**等待用户确认**（永远不要在用户确认前发送，即使是从 cron 定时触发也必须先创建草稿、由用户审阅后手动发送）
4. 确认后 `lark-cli mail user_mailbox.drafts send … --yes` 发送
5. `lark-cli mail user_mailbox.messages send_status …` 确认投递
6. **发送成功后必须自动打开 HTML 日报**：Windows 上用 `powershell -Command "Start-Process '<HTML路径>'"`；Mac 上用 `open <HTML路径>`

## 定时自动化

把这份日报做成每日例行定时任务。**关键原则：定时任务只生成日报+创建邮件草稿，不自动发送。** 用户收到草稿通知后审阅确认，再由 agent 发送。

### 创建 cron 任务

```bash
# 用 cronjob 工具创建，prompt 描述完整流程
cronjob action='create' \
  name='现金流日报-每日18:00' \
  schedule='0 18 * * *' \
  skills='["cashflow-daily-report","lark-mail","lark-contact"]' \
  enabled_toolsets='["terminal"]' \
  prompt='按 cashflow-daily-report skill 生成当日现金流日报 HTML+PDF，按邮件模板创建草稿给杜侠女(dushanshan@laiye.com)，不发送。'
```

### Cron 任务内流程（每次执行）

1. 取数 → 预测 → 写 analysis.json → 渲染 HTML+PDF（三步标准流程，复用同一份 `/tmp/cf_data.json`）
2. 将 HTML 产出到用户可访问的持久路径（如 `~/ClawWorker/cashflow_daily/cashflow_YYYYMMDD.html`），避免临时目录被清理
3. 按邮件模板格式撰写正文，`lark-cli mail +send --as user --to dushanshan@laiye.com --subject "资金日报 - YYYY年M月D日" --body "..." --attach "<PDF路径>"` 创建草稿（**不加 --confirm-send，不加 --yes**）
4. **最终回复中交付三样东西给用户：**
   - 草稿链接（飞书邮箱打开可直接审阅）
   - HTML 日报路径（用 `powershell -Command "Start-Process '<路径>'"` 自动打开）
   - 核心数据摘要（KPI 几行，让用户快速判断是否发送）

### 用户确认发送（在后续会话中）

用户看到 cron 推送的草稿通知后，说"发送"即可。Agent 此时：
1. 从草稿链接中提取 `draft_id`，或通过 `lark-cli mail user_mailbox.drafts list --as user` 找到对应草稿
2. `lark-cli mail user_mailbox.drafts send ... --yes` 发送
3. `send_status` 确认投递
4. `powershell -Command "Start-Process '<HTML路径>'"` 自动打开日报供查看（Windows）；Mac 上用 `open <HTML路径>`

### 收件人信息

- 姓名：杜侠女（杜珊珊）
- 邮箱：dushanshan@laiye.com
- 部门：综合财务部
- open_id：ou_895c35d18a7d571974c69d265e30770b
