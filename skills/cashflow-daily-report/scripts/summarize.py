#!/usr/bin/env python3
"""现金流日报 · 聊天问答输出（两个独立能力，可单独触发）。

把确定性的数字算好、排成 Markdown，agent 直接据此回答聊天，避免口算出错。
出 HTML 日报是另一个独立能力，用 render_report.py。

能力（互不依赖，各自会自己取数）:
  balances —— 查某日各账户回款 / 支出 / 当前余额
  forecast —— 看本月现金流预测完成情况

用法:
  python3 summarize.py --view balances [--date 2026-06-12] [--out /tmp/cf_data.json]
  python3 summarize.py --view forecast [--date 2026-06-12] [--analysis forecast.json]
  # --out/--data 仅用于同一轮里复用数据；不传也行，按日期取数是确定性的
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_bank_api as bank  # noqa: E402
from render_report import build_data, forecast_actual, large_txns, LARGE_IN, LARGE_OUT  # noqa: E402


def yuan(v):
    return f"¥{v:,.2f}"


def wan(v):
    return f"{v / 10000:,.1f}"


def step1(data):
    """第一步：三账户回款 / 支出 / 当前余额。"""
    L = [f"## {data['date']} · 三个银行账户：回款 / 支出 / 当前余额", "",
         "| 账户 | 账户类型 | 回款(当日流入) | 支出(当日流出) | 当前余额 |",
         "|---|---|--:|--:|--:|"]
    for a in data["accounts"]:
        L.append(f"| {a['bank']} | {a.get('account_type','')} | {yuan(a['total_inflow'])} "
                 f"| {yuan(a['total_outflow'])} | {yuan(a['closing_balance'])} |")
    g_in, g_out = data["group_total_inflow"], data["group_total_outflow"]
    L.append(f"| **合计** | | **{yuan(g_in)}** | **{yuan(g_out)}** | **{yuan(data['group_closing_balance'])}** |")
    net = data["group_net_cashflow"]
    L += ["", f"- 三账户合计当日**回款 {yuan(g_in)}、支出 {yuan(g_out)}**，"
              f"净{'流入' if net>=0 else '流出'} **{yuan(abs(net))}**，当前总余额 **{yuan(data['group_closing_balance'])}**。"]
    recv = large_txns(data, "收", LARGE_IN)
    pay = large_txns(data, "付", LARGE_OUT)
    if recv:
        L.append(f"- 其中大额回款（≥{LARGE_IN//10000}万）{len(recv)} 笔，最大："
                 f"{recv[0][1]} 收 {recv[0][0]['counterparty']} {yuan(recv[0][0]['amount'])}（{recv[0][0]['summary']}）。")
    if pay:
        L.append(f"- 大额支出（≥{LARGE_OUT//10000}万）{len(pay)} 笔，最大："
                 f"{pay[0][1]} 付 {pay[0][0]['counterparty']} {yuan(pay[0][0]['amount'])}（{pay[0][0]['summary']}）。")
    return "\n".join(L)


def step2(data, analysis):
    """第二步：本月资金预测完成情况。"""
    fa = forecast_actual(data, analysis)
    month = int(data["date"][5:7])
    L = [f"## {month} 月现金流预测完成情况（截至 {data['date']}）", "",
         "| 项目 | 流入 | 流出 | 净现金流 |", "|---|--:|--:|--:|",
         f"| 预测（本月） | {yuan(fa['f_in'])} | {yuan(fa['f_out'])} | {yuan(fa['f_net'])} |",
         f"| 实际（本月累计） | {yuan(fa['a_in'])} | {yuan(fa['a_out'])} | {yuan(fa['a_net'])} |",
         f"| 完成率 | {fa['rate_in']:.1f}% | {fa['rate_out']:.1f}% | {fa['rate_net']:.1f}% |", "",
         f"- **流入完成率 {fa['rate_in']:.1f}%**（实际累计回款 {wan(fa['a_in'])} 万 / 预测 {wan(fa['f_in'])} 万）。",
         f"- 净现金流完成 **{fa['rate_net']:.1f}%**（实际 {wan(fa['a_net'])} 万 / 预测净 {wan(fa['f_net'])} 万）。"]
    # 进度判断
    if fa["rate_net"] >= 50:
        L.append("- 判断：净现金流进度尚可。")
    else:
        L.append("- 判断：流入与净现金流进度偏慢，建议加快大额回款、控制非刚性支出以追平本月预算。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="现金流日报聊天问答输出（独立能力）")
    ap.add_argument("--view", choices=["balances", "forecast"], help="balances=回款/支出/余额；forecast=预测完成情况")
    ap.add_argument("--step", choices=["1", "2"], help="兼容别名：1=balances，2=forecast")
    ap.add_argument("--date", default=bank.CSV_DATE)
    ap.add_argument("--bank", default="all")
    ap.add_argument("--analysis", help="forecast 能力：含 forecast 的 JSON（可选，覆盖默认预测）")
    ap.add_argument("--data", help="复用已落地的数据 JSON（不再取数，仅同轮复用用）")
    ap.add_argument("--out", help="把本次取到的数据落地到该 JSON，供同轮其他命令复用")
    args = ap.parse_args()

    view = args.view or {"1": "balances", "2": "forecast"}.get(args.step)
    if not view:
        ap.error("需指定 --view balances|forecast（或别名 --step 1|2）")

    if args.data:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    else:
        date = bank.resolve_date(args.date)
        banks = list(bank.BANKS) if args.bank == "all" else [args.bank]
        data = build_data(date, banks)

    analysis = {}
    if args.analysis:
        with open(args.analysis, encoding="utf-8") as f:
            analysis = json.load(f)

    print(step1(data) if view == "balances" else step2(data, analysis))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
