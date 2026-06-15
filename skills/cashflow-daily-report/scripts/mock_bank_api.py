#!/usr/bin/env python3
"""银行流水数据接口 —— 现金流日报场景。

- **报告日**数据来自真实流水 CSV（`../data/transactions.csv`）：按账户聚合，
  期初余额由运行余额反推、期末余额取当日最后一笔。
- 为支撑「近 N 日趋势图」与「月度预算」，报告日以外的历史日按各账户**真实期初**
  确定性合成（仅贡献净额/余额，不展示明细）。
- 真实环境：把本文件替换为网银 / 财资系统 / 银企直连 API，下游逻辑不变。

用法:
  python3 mock_bank_api.py --bank all                 # 默认取 CSV 报告日
  python3 mock_bank_api.py --bank 招商银行 --date 2026-06-12 --pretty
  python3 mock_bank_api.py --date today               # today/yesterday/csv 关键字
金额单位: 元(CNY)。
"""
import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(HERE, "..", "data", "transactions.csv")

# 各账户资金安全线(元)；未配置的账户按期初余额 40% 估算
SAFE_LINES = {"招商银行": 2_000_000, "平安银行": 1_500_000, "兴业银行": 3_000_000}
# 账户类型；未配置默认对公活期
ACCOUNT_TYPES = {"招商银行": "对公活期", "平安银行": "对公活期", "兴业银行": "对公活期"}


def _num(s):
    s = (s or "").strip().replace(",", "").replace("，", "")
    return float(s) if s not in ("", "-") else 0.0


def _classify(category, summary):
    """现金流量表归类：利息→投资活动，其余→经营活动（本数据无筹资活动）。"""
    if "利息" in (summary or "") or "利息" in (category or ""):
        return "投资活动"
    return "经营活动"


def _load(path=DATA_CSV):
    raw = {}
    date = None
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bank = (row.get("主体") or "").strip()
            if not bank:
                continue
            r = (row.get("交易日期") or "").strip()
            dt = datetime.strptime(r, "%Y/%m/%d") if "/" in r else datetime.strptime(r, "%Y-%m-%d")
            date = dt.strftime("%Y-%m-%d")
            out_, in_ = _num(row.get("借（流出）")), _num(row.get("贷（流入）"))
            acc = raw.setdefault(bank, {"account": (row.get("账户") or "").strip(), "rows": []})
            acc["rows"].append({
                "amount": in_ if in_ > 0 else out_,
                "direction": "收" if in_ > 0 else "付",
                "category": (row.get("用途") or "").strip(),
                "summary": (row.get("摘要") or "").strip(),
                "counterparty": (row.get("对方账户名称") or "").strip(),
                "balance_after": _num(row.get("账户余额")),
            })

    result = {}
    for bank, acc in raw.items():
        rows = acc["rows"]
        txns = []
        for i, r in enumerate(rows):
            hh, mm = min(9 + i // 6, 18), (i * 9) % 60  # CSV 无具体时刻，按序铺到营业时段（仅展示用）
            txns.append({
                "seq": i + 1,
                "time": f"{date} {hh:02d}:{mm:02d}",
                "direction": r["direction"],
                "amount": r["amount"],
                "category": r["category"],
                "cashflow_type": _classify(r["category"], r["summary"]),
                "counterparty": r["counterparty"],
                "summary": r["summary"],
            })
        inflow = sum(t["amount"] for t in txns if t["direction"] == "收")
        outflow = sum(t["amount"] for t in txns if t["direction"] == "付")
        first = rows[0]
        first_net = first["amount"] if first["direction"] == "收" else -first["amount"]
        opening = round(first["balance_after"] - first_net, 2)
        result[bank] = {
            "bank": bank, "account": acc["account"],
            "opening": opening, "closing": round(rows[-1]["balance_after"], 2),
            "inflow": round(inflow, 2), "outflow": round(outflow, 2),
            "net": round(inflow - outflow, 2),
            "safe_line": SAFE_LINES.get(bank, round(opening * 0.4)),
            "txns": txns,
        }
    return date, result


CSV_DATE, _REAL = _load()
BANKS = list(_REAL.keys())  # 账户清单从 CSV 派生


def _lcg(bank, date):
    seed = int(hashlib.md5(f"{bank}|{date}".encode()).hexdigest(), 16) & ((1 << 64) - 1)
    st = [seed]

    def nxt(n):
        st[0] = (st[0] * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return st[0] % n

    return nxt


def gen_account(bank, date):
    real = _REAL[bank]
    acct_type = ACCOUNT_TYPES.get(bank, "对公活期")
    if date == CSV_DATE:
        return {
            "bank": bank, "account": real["account"], "account_type": acct_type,
            "date": date, "currency": "CNY",
            "opening_balance": real["opening"], "closing_balance": real["closing"],
            "total_inflow": real["inflow"], "total_outflow": real["outflow"],
            "net_cashflow": real["net"], "safe_line": real["safe_line"],
            "transaction_count": len(real["txns"]), "transactions": real["txns"],
        }
    # 历史日：以真实期初为基准确定性合成，仅供趋势图使用
    base, rnd = real["opening"], _lcg(bank, date)
    net = rnd(int(base * 0.16)) - int(base * 0.07)
    return {
        "bank": bank, "account": real["account"], "account_type": acct_type,
        "date": date, "currency": "CNY",
        "opening_balance": base, "closing_balance": round(base + net, 2),
        "total_inflow": max(net, 0), "total_outflow": max(-net, 0),
        "net_cashflow": net, "safe_line": real["safe_line"],
        "transaction_count": 0, "transactions": [],
    }


def resolve_date(s):
    if s in ("today", "今天"):
        return datetime.now().strftime("%Y-%m-%d")
    if s in ("yesterday", "昨天"):
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if s in ("csv", "data", "real"):
        return CSV_DATE
    datetime.strptime(s, "%Y-%m-%d")
    return s


def _wan(v):
    return f"{v / 10000:,.1f}"


def format_summary(result):
    """步骤一「查」用：回款/支出/当前余额的 markdown 汇总，便于在对话里直接展示。"""
    lines = [f"## {result['date']} 三账户资金查询（回款 / 支出 / 余额）", "",
             "| 账户 | 账户类型 | 回款(流入) | 支出(流出) | 当日净流入 | 当前余额(期末) |",
             "|---|---|--:|--:|--:|--:|"]
    for a in result["accounts"]:
        net = a["net_cashflow"]
        sign = "+" if net >= 0 else "−"
        lines.append(f"| {a['bank']} | {a.get('account_type', '')} | {_wan(a['total_inflow'])} | "
                     f"{_wan(a['total_outflow'])} | {sign}{_wan(abs(net))} | {_wan(a['closing_balance'])} |")
    gn = result["group_net_cashflow"]
    gsign = "+" if gn >= 0 else "−"
    lines.append(f"| **合计** | | **{_wan(result['group_total_inflow'])}** | "
                 f"**{_wan(result['group_total_outflow'])}** | **{gsign}{_wan(abs(gn))}** | "
                 f"**{_wan(result['group_closing_balance'])}**|")
    lines += ["", "单位：万元（人民币）。数据来源：银行流水接口。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="银行流水数据接口 (现金流日报)")
    ap.add_argument("--bank", default="all", help="银行名 或 all。可选: " + " / ".join(BANKS) + " / all")
    ap.add_argument("--date", default=CSV_DATE, help="YYYY-MM-DD 或 today/yesterday/csv")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--summary", action="store_true", help="输出回款/支出/余额的 markdown 汇总（步骤一用）")
    ap.add_argument("--out", help="把完整 JSON 落地到该文件，供步骤二 render_report.py --data 消费")
    args = ap.parse_args()

    date = resolve_date(args.date)
    if args.bank == "all":
        banks = list(BANKS)
    elif args.bank in BANKS:
        banks = [args.bank]
    else:
        print(json.dumps({"error": f"未知银行: {args.bank}", "available": list(BANKS) + ["all"]},
                         ensure_ascii=False))
        sys.exit(1)

    accounts = [gen_account(b, date) for b in banks]
    result = {
        "date": date, "account_count": len(accounts),
        "group_opening_balance": round(sum(a["opening_balance"] for a in accounts), 2),
        "group_closing_balance": round(sum(a["closing_balance"] for a in accounts), 2),
        "group_total_inflow": round(sum(a["total_inflow"] for a in accounts), 2),
        "group_total_outflow": round(sum(a["total_outflow"] for a in accounts), 2),
        "group_net_cashflow": round(sum(a["net_cashflow"] for a in accounts), 2),
        "accounts": accounts,
    }
    if args.out:  # 落地完整 JSON，供步骤二 render_report.py --data 消费
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if args.summary:
        print(format_summary(result))
    elif args.out:
        print(args.out)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
