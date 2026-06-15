#!/usr/bin/env python3
"""现金流日报 → 自包含 HTML 仪表盘（对齐「资金日报」模板）。

从银行流水接口取数、计算全部指标、合并 agent 给的分析文字，
产出一个**零外链、双击即开、浅/深色自适应**的 .html 文件（可选 --pdf）。

报告结构对齐模板：
  总览(总期初/总流入/总流出/当日净流入/总期末) → 本月资金预测(预测vs实际+流入完成率)
  → 账户资金明细汇总(含账户类型+合计行) → 重点关注(大额回款≥30万 / 大额支出≥5万 / 月度预算完成)
  → 资金趋势(近14日, value-add) → 收支结构(value-add) → 分析与建议

analysis.json 结构（全部可选）:
  {
    "company": "XX集团",
    "summary": "一句话总览",
    "judgments": ["【判断】... (依据: ...)", "..."],
    "recommendations": ["建议1", "建议2"],
    "yesterday": {"group_closing_balance": 16000000},
    "forecast": {"inflow": 12000000, "outflow": 5000000, "net": 7000000}
  }
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_bank_api as bank  # noqa: E402

TREND_DAYS = 14  # 资金趋势回看天数
DEFAULT_FORECAST = {"inflow": 12_000_000, "outflow": 5_000_000, "net": 7_000_000}  # 本月资金预测默认
LARGE_IN = 300_000   # 大额回款阈值（元）
LARGE_OUT = 50_000   # 大额支出阈值（元）


def wan(v):
    return f"{v / 10000:,.1f}"


def signed_wan(v):
    s = "+" if v >= 0 else "−"
    return f"{s}{abs(v) / 10000:,.1f}"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def cashflow_structure(data):
    types = ["经营活动", "投资活动", "筹资活动"]
    agg = {t: {"in": 0, "out": 0} for t in types}
    for acc in data["accounts"]:
        for t in acc["transactions"]:
            key = "in" if t["direction"] == "收" else "out"
            agg[t["cashflow_type"]][key] += t["amount"]
    return agg


def large_txns(data, direction, threshold):
    out = []
    for acc in data["accounts"]:
        for t in acc["transactions"]:
            if t["direction"] == direction and t["amount"] >= threshold:
                out.append((t, acc["bank"]))
    out.sort(key=lambda x: x[0]["amount"], reverse=True)
    return out


def build_data(date, banks):
    """取数 + 集团汇总，返回统一的 data 结构（三步共用）。"""
    accounts = [bank.gen_account(b, date) for b in banks]
    return {
        "date": date, "account_count": len(accounts),
        "group_opening_balance": round(sum(a["opening_balance"] for a in accounts), 2),
        "group_closing_balance": round(sum(a["closing_balance"] for a in accounts), 2),
        "group_total_inflow": round(sum(a["total_inflow"] for a in accounts), 2),
        "group_total_outflow": round(sum(a["total_outflow"] for a in accounts), 2),
        "group_net_cashflow": round(sum(a["net_cashflow"] for a in accounts), 2),
        "accounts": accounts,
    }


def forecast_actual(data, analysis):
    fc = {**DEFAULT_FORECAST, **(analysis.get("forecast") or {})}
    f_in, f_out = fc["inflow"], fc["outflow"]
    f_net = fc.get("net", f_in - f_out)
    a_in, a_out, a_net = data["group_total_inflow"], data["group_total_outflow"], data["group_net_cashflow"]
    return {
        "f_in": f_in, "f_out": f_out, "f_net": f_net,
        "a_in": a_in, "a_out": a_out, "a_net": a_net,
        "rate_in": (a_in / f_in * 100) if f_in else 0,
        "rate_out": (a_out / f_out * 100) if f_out else 0,
        "rate_net": (a_net / f_net * 100) if f_net else 0,
    }


def auto_analysis(data, analysis):
    """无 agent 分析时的规则化兜底，保证 HTML 永不空白。"""
    net = data["group_net_cashflow"]
    fa = forecast_actual(data, analysis)
    month = int(data["date"][5:7])
    j, rec = [], []
    direction = "净流入" if net >= 0 else "净流出"
    j.append(f"【判断】集团当日{direction} {wan(abs(net))} 万元，总期末余额 "
             f"{wan(data['group_closing_balance'])} 万元（流入 {wan(data['group_total_inflow'])} / "
             f"流出 {wan(data['group_total_outflow'])} 万元）。")
    recv = large_txns(data, "收", LARGE_IN)
    pay = large_txns(data, "付", LARGE_OUT)
    if recv:
        t, bnk = recv[0]
        j.append(f"【判断】当日大额回款 {len(recv)} 笔，最大为 {t['counterparty']} "
                 f"{wan(t['amount'])} 万元（{t['summary']}）。")
    if pay:
        t, bnk = pay[0]
        j.append(f"【判断】当日大额支出 {len(pay)} 笔，最大为 {t['counterparty']} "
                 f"{wan(t['amount'])} 万元（{t['summary']}）。")
    j.append(f"【判断】{month} 月累计净流入 {signed_wan(net)} 万元，完成月度计划目标（净）"
             f"{fa['rate_net']:.1f}%、流入完成率 {fa['rate_in']:.1f}%。"
             f"（依据：预测净 {wan(fa['f_net'])} / 预测流入 {wan(fa['f_in'])} 万元）")
    breached = [a for a in data["accounts"] if a["closing_balance"] < a["safe_line"]]
    if breached:
        names = "、".join(a["bank"] for a in breached)
        j.append(f"【判断】{names} 期末余额跌破安全线，需立即处置。")
        rec.append(f"建议向 {names} 调拨资金补足安全垫，暂缓非刚性支出。")
    if fa["rate_net"] < 50:
        rec.append("月度净现金流进度偏慢，建议加快大额回款、控制非刚性支出以追平预算。")
    if not rec:
        rec.append("当前各账户余额充裕、无破线风险，维持现有头寸；闲置资金可考虑短期理财。")
    summary = (f"集团当日{direction} {wan(abs(net))} 万元，总期末 "
               f"{wan(data['group_closing_balance'])} 万元；{month} 月累计净流入完成计划 "
               f"{fa['rate_net']:.1f}%、流入完成率 {fa['rate_in']:.1f}%。")
    return {"summary": summary, "judgments": j, "recommendations": rec}


CSS = """
:root{
  --bg:#f4f6f9; --surface:#ffffff; --ink:#15212e; --muted:#5b6b7b; --line:#e4e9f0;
  --accent:#1f6feb; --in:#15904a; --in-bg:#e7f6ed; --out:#c0392b; --out-bg:#fbeceb;
  --warn:#9a6700; --warn-bg:#fff4d6; --bad:#a3201f; --bad-bg:#fbe2e1; --ok:#15904a; --ok-bg:#e7f6ed;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0f1620; --surface:#16202c; --ink:#e8edf3; --muted:#9fb0c0; --line:#243140;
    --accent:#5b9bff; --in:#4cc985; --in-bg:#11301f; --out:#f08079; --out-bg:#321614;
    --warn:#e7b94e; --warn-bg:#32280d; --bad:#f08079; --bad-bg:#321614; --ok:#4cc985; --ok-bg:#11301f; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.6;}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 56px;}
.head{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;margin-bottom:24px;}
.head h1{font-size:24px;font-weight:600;margin:0;letter-spacing:.5px;}
.head .sub{color:var(--muted);font-size:14px;margin-top:4px;}
.tag{display:inline-block;background:var(--accent);color:#fff;font-size:12px;
  padding:3px 10px;border-radius:20px;vertical-align:middle;margin-left:8px;font-weight:500;}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:14px;margin-bottom:26px;}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;}
.kpi .label{color:var(--muted);font-size:13px;}
.kpi .val{font-size:24px;font-weight:600;margin-top:6px;}
.kpi .unit{font-size:13px;color:var(--muted);font-weight:400;margin-left:3px;}
.kpi .delta{font-size:12px;margin-top:4px;}
.pos{color:var(--in)} .neg{color:var(--out)}
section{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:20px 22px;margin-bottom:20px;}
section h2{font-size:16px;font-weight:600;margin:0 0 16px;display:flex;align-items:center;gap:8px;}
section h2 .bar{width:4px;height:16px;background:var(--accent);border-radius:2px;display:inline-block;}
.subh{font-weight:600;font-size:14px;margin:18px 0 8px;}
.subh:first-of-type{margin-top:0;}
table{width:100%;border-collapse:collapse;font-size:14px;}
th,td{text-align:right;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap;}
th:first-child,td:first-child{text-align:left;}
th{color:var(--muted);font-weight:500;font-size:13px;}
tbody tr:last-child td{border-bottom:none;}
tr.total td{font-weight:600;border-top:2px solid var(--line);}
.badge{display:inline-block;font-size:12px;padding:2px 9px;border-radius:20px;font-weight:500;}
.badge.ok{background:var(--ok-bg);color:var(--ok)} .badge.warn{background:var(--warn-bg);color:var(--warn)}
.badge.bad{background:var(--bad-bg);color:var(--bad)}
.chart-row{display:flex;align-items:center;gap:12px;margin:10px 0;font-size:13px;}
.chart-row .name{width:64px;color:var(--muted);flex:none;}
.chart-row .track{flex:1;}
.alist{margin:0;padding:0;list-style:none;}
.alist li{padding:10px 0;border-bottom:1px solid var(--line);font-size:14px;}
.alist li:last-child{border-bottom:none;}
.rec li{position:relative;padding-left:22px;}
.rec li:before{content:"→";position:absolute;left:0;color:var(--accent);font-weight:600;}
.foot{color:var(--muted);font-size:12px;text-align:center;margin-top:28px;line-height:1.7;}
.summary{font-size:15px;background:var(--in-bg);border-left:4px solid var(--accent);
  padding:12px 16px;border-radius:8px;color:var(--ink);}
.pbar{position:relative;height:14px;background:var(--line);border-radius:8px;margin:12px 0 4px;}
.pfill{height:100%;border-radius:8px;}
@media print{
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact;}
  .wrap{max-width:none;padding:0;}
  section,.kpi{break-inside:avoid;}
  @page{margin:14mm 12mm;}
}
"""


def compute_trend(banks, end_date, days=TREND_DAYS):
    """往前推 days 天，逐日汇总集团期末余额与净现金流（确定性）。"""
    end = datetime.strptime(end_date, "%Y-%m-%d")
    series = []
    for k in range(days - 1, -1, -1):
        d = (end - timedelta(days=k)).strftime("%Y-%m-%d")
        accts = [bank.gen_account(b, d) for b in banks]
        series.append({
            "date": d,
            "bal": sum(a["closing_balance"] for a in accts),
            "net": sum(a["net_cashflow"] for a in accts),
        })
    return series


def svg_trend(series):
    """近 N 日资金趋势：上方期末余额折线 + 下方每日净现金流柱。纯内联 SVG。"""
    n = len(series)
    L, R = 50, 584
    step = (R - L) / max(1, n - 1)
    bals = [s["bal"] for s in series]
    lo, hi = min(bals), max(bals)
    rng = (hi - lo) or (hi * 0.02 or 1)
    pad = rng * 0.15
    lo, hi = lo - pad, hi + pad
    bt, bb = 18, 150

    def x(i):
        return L + step * i

    def y(b):
        return bt + (1 - (b - lo) / (hi - lo)) * (bb - bt)

    grid = []
    for frac, val in ((0, hi), (0.5, (hi + lo) / 2), (1.0, lo)):
        yy = bt + frac * (bb - bt)
        grid.append(f'<line x1="{L}" y1="{yy:.1f}" x2="{R}" y2="{yy:.1f}" stroke="var(--line)" stroke-width="1"/>'
                    f'<text x="{L-6:.0f}" y="{yy+4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{val/10000:,.0f}</text>')

    pts = " ".join(f"{x(i):.1f},{y(s['bal']):.1f}" for i, s in enumerate(series))
    area = f"{L},{bb} {pts} {R},{bb}"
    dots = "".join(f'<circle cx="{x(i):.1f}" cy="{y(s["bal"]):.1f}" r="2.5" fill="var(--accent)"/>'
                   for i, s in enumerate(series))
    last = series[-1]
    lx, ly = x(n - 1), y(last["bal"])
    last_lbl = (f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="var(--accent)"/>'
                f'<text x="{lx-4:.1f}" y="{ly-9:.1f}" text-anchor="end" font-size="12" font-weight="600" '
                f'fill="var(--accent)">{last["bal"]/10000:,.0f}万</text>')

    nt, nb = 178, 236
    zero = (nt + nb) / 2
    maxabs = max((abs(s["net"]) for s in series), default=1) or 1
    half = (nb - zero) * 0.92
    bw = max(3, step * 0.45)
    bars = [f'<line x1="{L}" y1="{zero:.1f}" x2="{R}" y2="{zero:.1f}" stroke="var(--line)" stroke-width="1"/>',
            f'<text x="{L-6}" y="{nt+4}" text-anchor="end" font-size="10" fill="var(--muted)">净流入</text>',
            f'<text x="{L-6}" y="{nb}" text-anchor="end" font-size="10" fill="var(--muted)">净流出</text>']
    for i, s in enumerate(series):
        h = (s["net"] / maxabs) * half
        yb = zero - h if h >= 0 else zero
        col = "var(--in)" if s["net"] >= 0 else "var(--out)"
        bars.append(f'<rect x="{x(i)-bw/2:.1f}" y="{yb:.1f}" width="{bw:.1f}" height="{abs(h):.1f}" rx="1" fill="{col}"/>')

    xlabels = []
    show = {0, n - 1, n // 2, n // 4, (3 * n) // 4}
    for i in show:
        xlabels.append(f'<text x="{x(i):.1f}" y="250" text-anchor="middle" font-size="10" '
                       f'fill="var(--muted)">{series[i]["date"][5:]}</text>')

    return (f'<svg viewBox="0 0 600 256" width="100%" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="近{n}日集团期末可用余额走势与每日净现金流">'
            f'{"".join(grid)}'
            f'<polygon points="{area}" fill="var(--accent)" fill-opacity="0.08"/>'
            f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>{dots}{last_lbl}'
            f'{"".join(bars)}{"".join(xlabels)}</svg>')


def svg_bar(value, maxv, color):
    pct = 0 if maxv == 0 else max(2, round(value / maxv * 100))
    return (f'<svg width="100%" height="16" viewBox="0 0 100 16" preserveAspectRatio="none">'
            f'<rect x="0" y="3" width="100" height="10" rx="3" fill="var(--line)"/>'
            f'<rect x="0" y="3" width="{pct}" height="10" rx="3" fill="{color}"/></svg>')


def _txn_table(items, party_label, empty):
    if not items:
        return f'<p style="color:var(--muted);font-size:14px">{empty}</p>'
    rows = "".join(
        f"<tr><td>{esc(t['time'][11:])}</td><td>{esc(bnk)}</td><td>{esc(t['counterparty'])}</td>"
        f"<td>{esc(t['summary'] or t['category'])}</td><td><b>{wan(t['amount'])}</b></td></tr>"
        for t, bnk in items)
    return (f"<table><thead><tr><th>时间</th><th>账户</th><th>{party_label}</th><th>项目</th>"
            f"<th>金额(万)</th></tr></thead><tbody>{rows}</tbody></table>")


def render(data, analysis):
    company = analysis.get("company", "公司")
    yest = analysis.get("yesterday") or {}
    bal_delta = txt_delta(data["group_closing_balance"], yest.get("group_closing_balance"))
    net = data["group_net_cashflow"]
    month = int(data["date"][5:7])

    # 总览 KPI（对齐模板：总期初/总流入/总流出/当日净流入/总期末）
    kpis = [
        ("总期初余额", wan(data["group_opening_balance"]), "万元", ""),
        ("当日总流入", wan(data["group_total_inflow"]), "万元", '<span class="pos">收入</span>'),
        ("当日总流出", wan(data["group_total_outflow"]), "万元", '<span class="neg">支出</span>'),
        ("当日净流入", signed_wan(net), "万元",
         f'<span class="{"pos" if net>=0 else "neg"}">{"净流入" if net>=0 else "净流出"}</span>'),
        ("总期末余额", wan(data["group_closing_balance"]), "万元", bal_delta),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="label">{esc(l)}</div>'
        f'<div class="val">{v}<span class="unit">{u}</span></div>'
        f'<div class="delta">{d}</div></div>'
        for l, v, u, d in kpis)

    # 本月资金预测（预测 vs 实际 + 流入完成率）
    fa = forecast_actual(data, analysis)
    fc_table = (
        '<table><thead><tr><th>项目</th><th>流入</th><th>流出</th><th>净现金流</th></tr></thead><tbody>'
        f'<tr><td>预测（本月）</td><td>{wan(fa["f_in"])}</td><td>{wan(fa["f_out"])}</td><td>{wan(fa["f_net"])}</td></tr>'
        f'<tr><td>实际（本月累计）</td><td class="pos">{wan(fa["a_in"])}</td><td class="neg">{wan(fa["a_out"])}</td>'
        f'<td class="{"pos" if fa["a_net"]>=0 else "neg"}">{signed_wan(fa["a_net"])}</td></tr>'
        f'<tr class="total"><td>完成率</td><td>{fa["rate_in"]:.1f}%</td><td>{fa["rate_out"]:.1f}%</td>'
        f'<td>{fa["rate_net"]:.1f}%</td></tr></tbody></table>')
    forecast_html = (
        '<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px">'
        f'<div style="font-size:22px;font-weight:600">流入完成率 {fa["rate_in"]:.1f}%</div>'
        f'<div style="color:var(--muted);font-size:13px">实际累计流入 {wan(fa["a_in"])} / 预测 {wan(fa["f_in"])} 万元</div></div>'
        f'<div class="pbar"><div class="pfill" style="width:{min(100, max(0, fa["rate_in"])):.1f}%;background:var(--accent)"></div></div>'
        f'<div style="margin-top:14px">{fc_table}</div>')

    # 账户资金明细汇总（账户类型 + 合计行）
    rows = []
    for a in data["accounts"]:
        rows.append(
            f"<tr><td>{esc(a['bank'])}</td><td>{esc(a.get('account_type',''))}</td>"
            f"<td style='color:var(--muted);font-size:12px'>{esc(a['account'])}</td>"
            f"<td>{wan(a['opening_balance'])}</td>"
            f"<td class='pos'>{wan(a['total_inflow'])}</td>"
            f"<td class='neg'>{wan(a['total_outflow'])}</td>"
            f"<td class='{'pos' if a['net_cashflow']>=0 else 'neg'}'>{signed_wan(a['net_cashflow'])}</td>"
            f"<td><b>{wan(a['closing_balance'])}</b></td></tr>")
    rows.append(
        f"<tr class='total'><td>合计</td><td></td><td></td><td>{wan(data['group_opening_balance'])}</td>"
        f"<td class='pos'>{wan(data['group_total_inflow'])}</td>"
        f"<td class='neg'>{wan(data['group_total_outflow'])}</td>"
        f"<td class='{'pos' if net>=0 else 'neg'}'>{signed_wan(net)}</td>"
        f"<td>{wan(data['group_closing_balance'])}</td></tr>")
    pos_table = ("<table><thead><tr><th>账户名称</th><th>账户类型</th><th>账号</th><th>期初</th>"
                 "<th>流入</th><th>流出</th><th>净额</th><th>期末</th></tr></thead><tbody>"
                 + "".join(rows) + "</tbody></table>")

    # 重点关注：大额回款 / 大额支出 / 月度预算完成
    recv = large_txns(data, "收", LARGE_IN)
    pay = large_txns(data, "付", LARGE_OUT)
    recv_table = _txn_table(recv, "客户", f"当日无 ≥{LARGE_IN // 10000} 万元的大额回款。")
    pay_table = _txn_table(pay, "供应商", f"当日无 ≥{LARGE_OUT // 10000} 万元的大额支出。")
    budget_line = (
        f'{month} 月累计净流入 <b class="{"pos" if net>=0 else "neg"}">{signed_wan(net)}</b> 万元，'
        f'完成月度计划目标（净现金流）<b>{fa["rate_net"]:.1f}%</b>'
        f'（预测净 {wan(fa["f_net"])} 万元，流入完成率 {fa["rate_in"]:.1f}%）。')
    focus_html = (
        f'<div class="subh">⏳ 大额回款提示（客户单笔 ≥ {LARGE_IN // 10000} 万元）</div>{recv_table}'
        f'<div class="subh">⏳ 大额支出提示（供应商单笔 ≥ {LARGE_OUT // 10000} 万元）</div>{pay_table}'
        f'<div class="subh">✅ 月度预算完成情况</div>'
        f'<div style="font-size:14px">{budget_line}</div>')

    # 资金趋势折线图（value-add）
    trend = compute_trend([a["bank"] for a in data["accounts"]], data["date"])
    trend_html = svg_trend(trend)
    tb_delta = trend[-1]["bal"] - trend[0]["bal"]
    trend_cap = (f'近 {len(trend)} 日集团期末可用余额由 {wan(trend[0]["bal"])} 万元 '
                 f'<span class="{"pos" if tb_delta>=0 else "neg"}">{signed_wan(tb_delta)} 万元</span> '
                 f'至 {wan(trend[-1]["bal"])} 万元')

    # 收支结构条形图（value-add）
    agg = cashflow_structure(data)
    maxv = max([max(v["in"], v["out"]) for v in agg.values()] + [1])
    chart = []
    for t, v in agg.items():
        net_t = v["in"] - v["out"]
        chart.append(
            f'<div style="margin-bottom:14px"><div style="font-size:13px;margin-bottom:4px">'
            f'<b>{esc(t)}</b> · 净 <span class="{"pos" if net_t>=0 else "neg"}">{signed_wan(net_t)}</span> 万元</div>'
            f'<div class="chart-row"><span class="name pos">流入</span><span class="track">{svg_bar(v["in"], maxv, "var(--in)")}</span>'
            f'<span style="width:72px;text-align:right">{wan(v["in"])}万</span></div>'
            f'<div class="chart-row"><span class="name neg">流出</span><span class="track">{svg_bar(v["out"], maxv, "var(--out)")}</span>'
            f'<span style="width:72px;text-align:right">{wan(v["out"])}万</span></div></div>')
    chart_html = "".join(chart)

    # 分析与建议
    judg = "".join(f"<li>{esc(x)}</li>" for x in analysis.get("judgments", [])) or "<li>无</li>"
    recs = "".join(f"<li>{esc(x)}</li>" for x in analysis.get("recommendations", [])) or "<li>无</li>"
    summary = esc(analysis.get("summary", ""))

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>现金流日报 · {esc(data['date'])}</title><style>{CSS}</style></head>
<body><div class="wrap">
  <div class="head">
    <div><h1>现金流日报<span class="tag">Agent 自动生成</span></h1>
      <div class="sub">Cash Flow Daily Report · {esc(company)} · {esc(data['date'])} · 单位：人民币元（表中万元）</div></div>
  </div>
  {f'<div class="summary">{summary}</div>' if summary else ''}
  <div class="kpis" style="margin-top:18px">{kpi_html}</div>
  <section><h2><span class="bar"></span>本月资金预测</h2>{forecast_html}</section>
  <section><h2><span class="bar"></span>账户资金明细汇总</h2>{pos_table}</section>
  <section><h2><span class="bar"></span>重点关注事项</h2>{focus_html}</section>
  <section><h2><span class="bar"></span>资金趋势（近 {len(trend)} 日）</h2>
    {trend_html}
    <div style="color:var(--muted);font-size:13px;margin-top:8px">{trend_cap}</div></section>
  <section><h2><span class="bar"></span>收支结构（经营 / 投资 / 筹资）</h2>{chart_html}</section>
  <section><h2><span class="bar"></span>分析与建议</h2>
    <ul class="alist">{judg}</ul>
    <div style="margin-top:14px;font-weight:600;font-size:14px;color:var(--muted)">行动建议</div>
    <ul class="alist rec">{recs}</ul>
  </section>
  <div class="foot">本报告由 Agent 基于银行流水接口数据自动生成 · 金额单位：人民币元（表中显示为万元）<br>
  演示数据来源于虚拟流水，非真实资金信息。</div>
</div></body></html>"""


def txt_delta(today, yest):
    if yest is None:
        return '<span style="color:var(--muted)">无环比</span>'
    d = today - yest
    cls = "pos" if d >= 0 else "neg"
    return f'<span class="{cls}">环比 {signed_wan(d)} 万元</span>'


def find_chrome():
    """找一个可用的 headless Chromium/Chrome（系统 Chrome 或 Playwright 缓存）。"""
    import glob
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        # Windows
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
        "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    ]
    cands += sorted(glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing")), reverse=True)
    cands += sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")), reverse=True)
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def html_to_pdf(html_path, pdf_path):
    """headless Chrome 打印 PDF，保真渲染（含 CSS/SVG）。"""
    import subprocess
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Chromium，无法生成 PDF。请安装 Google Chrome。")
    subprocess.run([
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=4000",
        f"--print-to-pdf={pdf_path}", f"file://{html_path}",
    ], check=True, capture_output=True, timeout=120)
    return pdf_path


def main():
    ap = argparse.ArgumentParser(description="现金流日报 HTML 生成器")
    ap.add_argument("--date", default=bank.CSV_DATE, help="YYYY-MM-DD 或 today/yesterday/csv（默认=CSV 报告日）")
    ap.add_argument("--bank", default="all")
    ap.add_argument("--data", help="步骤一已取数的 JSON（mock_bank_api --out 的产物）；给了就不再取数")
    ap.add_argument("--analysis", help="分析 JSON 文件路径（agent 写的判断/建议）")
    ap.add_argument("--out", help="输出 HTML 路径（默认 ~/cashflow_<date>.html）")
    ap.add_argument("--pdf", action="store_true", help="同时用 headless Chrome 导出同名 PDF")
    args = ap.parse_args()

    if args.data:  # 步骤二：直接消费步骤一落地的数据，不重新取数
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
        date = data["date"]
    else:
        date = bank.resolve_date(args.date)
        banks = list(bank.BANKS) if args.bank == "all" else [args.bank]
        data = build_data(date, banks)

    analysis = {}
    if args.analysis:
        with open(args.analysis, encoding="utf-8") as f:
            analysis = json.load(f)
    if not analysis.get("judgments"):
        analysis = {**auto_analysis(data, analysis), **analysis}

    out = args.out or os.path.expanduser(f"~/cashflow_{date}.html")
    out_abs = os.path.abspath(out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(data, analysis))
    # 同时打印裸路径与跨平台可点击的 file:// 链接（Windows→file:///C:/...）
    print(out_abs)
    print("HTML_URL:", Path(out_abs).as_uri())
    if args.pdf:
        pdf_path = os.path.splitext(out_abs)[0] + ".pdf"
        html_to_pdf(out_abs, pdf_path)
        print(pdf_path)
        print("PDF_URL:", Path(pdf_path).as_uri())


if __name__ == "__main__":
    main()
