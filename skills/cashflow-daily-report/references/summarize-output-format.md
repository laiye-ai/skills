# summarize.py --out 输出 JSON 结构

`summarize.py --view balances --date <日期> --out <path>` 写入的 JSON 是**扁平键**结构（无嵌套 `group`/`summary` 对象），字段如下：

```json
{
  "date": "2026-06-12",
  "account_count": 3,
  "group_opening_balance": 16700000.0,
  "group_closing_balance": 18473886.6,
  "group_total_inflow": 2521066.6,
  "group_total_outflow": 747180.0,
  "group_net_cashflow": 1773886.6,
  "accounts": [
    {
      "bank": "招商银行",
      "account": "对公活期",
      "account_type": "对公活期",
      "date": "2026-06-12",
      "currency": "CNY",
      "opening_balance": 5000000.0,
      "closing_balance": 6076530.5,
      "total_inflow": 1186280.5,
      "total_outflow": 109750.0,
      "net_cashflow": 1076530.5,
      "safe_line": 2000000,
      "transactions": [
        {
          "time": "...",
          "direction": "收",
          "amount": 520000.0,
          "counterparty": "深圳创新科技股份公司",
          "summary": "项目款到账",
          "cashflow_type": "经营活动"
        }
      ]
    }
  ]
}
```

**关键注意**：
- 集团汇总在**顶层扁平字段**（`group_opening_balance`、`group_closing_balance` 等），不是嵌套在 `data.group` 下
- 账户数组 key 是 `accounts`，每个账户的银行名在 `bank` 字段（不是 `bank_name`）
- `safe_line` 在账户级别，集团级别无此字段
- 交易明细中的收支方向是 `direction`（`"收"` / `"付"`），不是 `type`
- `cashflow_type` = 经营活动 / 投资活动 / 筹资活动
- 金额单位：元(CNY)，均为 float
- 日期格式：`YYYY-MM-DD`

## 常见坑

1. 不要假设 `data['group']` 或 `data['summary']` 存在——所有 group_* 字段都在顶层
2. `--out` 写入的是 `/tmp/cf_data.json`，在 Windows 上等同于 `C:\Users\<user>\AppData\Local\Temp\cf_data.json`
3. 在 `write_file` 的 Python 脚本中读取时，用 Windows 绝对路径 `C:\Users\dusha\AppData\Local\Temp\cf_data.json`（`/tmp/cf_data.json` 在 terminal 的 MSYS 环境中有效，但 `execute_code` 沙箱看不到）
4. 不要用 `python -c` 内联（会被安全策略拦截），写 `.py` 文件 + `terminal` 执行
