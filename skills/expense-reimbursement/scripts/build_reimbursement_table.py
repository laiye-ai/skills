#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把匹配分组 + 出差申请记录，拼成报销单（markdown 表 + 结构化记录）。

输入:
  groups.json   —— match_rename.py 产出
  --trips t.json —— 出差申请记录（由 lark-base 从「报销流程场景/出差申请」表读出），
                    形如 [{"编号":"1","标题":"长沙外勤","出发日期":"2026-05-25","结束日期":"2026-05-29","出发地":"北京","目的地":"长沙"}, ...]
  --date 2026-06-12 —— 报销日期（默认 <提交当日> 占位，由 agent 填实际提交日）

规则要点（以发票为单位，一发票一行；除消费日期外取发票数据）:
  报销人/部门 取 config；报销金额=价税合计；金额/税额/主体(购买方)/销售方/项目明细 取发票；
  消费日期：有补充材料取补充材料日期，否则取发票日期；
  差旅费需关联出差申请（开始/结束日期匹配 或 出发地/目的地匹配）：
    机票/火车额外填出发地、目的地；住宿额外填开始、结束日期；
  业务招待费额外填招待人数（来自水单）、招待客户（水单无 -> 待补充）。

输出:
  stdout: markdown 报销单表
  --json out.json: 结构化记录数组，供 lark-base 写入「待主管审批」视图

系统/流程列（报销单号、审批人、打款、创建/更新等）留空，由飞书审批流填。
"""
import argparse
import json
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.normpath(os.path.join(HERE, "..", "config.json"))

HEADERS = [
    "报销单号", "发票号码", "更新时间", "创建人", "关联出差申请编号", "修改人",
    "结束日期", "目的地", "报销金额", "税额", "创建时间", "打款日期",
    "销售方名称", "出发地", "一级费用类型", "报销人", "报销主体", "打款状态",
    "报销日期", "附件", "项目明细", "消费日期", "收款银行", "主管审批人",
    "审批状态", "二级费用明细", "开始日期", "金额", "财务经办人", "打款备注",
    "招待人数", "收款账号", "招待客户", "收款人", "报销事由", "费用承担部门",
]
BLANK = {"报销单号", "主管审批人", "财务经办人", "审批状态", "打款状态", "打款日期",
         "打款备注", "创建时间", "创建人", "更新时间", "修改人"}

# 给用户确认的「预览」只展示这些核心业务列（需核对 / 待补填的字段）。
# 固定信息（报销人/费用承担部门/收款信息/报销日期）在表上方一行说明，不逐行重复；
# 系统流程列、不含税金额/税额等明细不进预览，但 --json 写回飞书仍是完整 36 列。
PREVIEW = [
    "报销人", "报销事由", "一级费用类型", "二级费用明细", "报销金额", "消费日期",
    "报销主体", "销售方名称", "项目明细", "发票号码",
    "出发地", "目的地", "开始日期", "结束日期",
    "招待人数", "招待客户", "关联出差申请编号", "附件",
]

# 报销人/收款人取单据上的「个人名」：购买方名称里排除机构名（带这些字样视为公司）
COMPANY_KW = ("公司", "有限", "科技", "集团", "中心", "厂", "店", "酒店", "银行",
              "铁路", "出行", "旅行社", "管理", "服务", "商行", "超市", "餐饮", "网络")
_account_cache = {}


PERSON_FIELDS = ("乘车人", "旅客", "出行人", "购买方名称")  # 真实对应人名字段优先级（铁路=乘车人/机票=旅客/购买方个人名兜底）


def is_person(name):
    n = clean(name)
    return bool(n) and 2 <= len(n) <= 4 and not any(k in n for k in COMPANY_KW)


def detect_person(g):
    """该行单据上的真实对应人名：先乘车人/出行人，再购买方个人名。
    本行单据上找不到真实人名就返回「待补充」——不回退、不借用其它行的人名。"""
    for x in (g.get("supplement"), g.get("invoice")):
        obj = x.get("_obj") if x else None
        if not obj:
            continue
        for key in PERSON_FIELDS:
            if is_person(fld(obj, key)):
                return clean(fld(obj, key))
    return "待补充"


def account_for(name, digits):
    """同一收款人复用同一随机账号（演示用假号，位数按 config）；无真实人名则收款账号也待补充。"""
    if not name or name == "待补充":
        return "待补充"
    if name not in _account_cache:
        _account_cache[name] = str(random.randint(10 ** (digits - 1), 10 ** digits - 1))
    return _account_cache[name]

CAT = {  # kind -> (一级消费类型, 二级消费类型)
    "hotel": ("差旅费", "住宿"),
    "flight": ("差旅费", "机票"),
    "didi": ("交通费", "市内交通(网约车)"),
    "meal": ("业务招待费", "餐饮"),
}


def fld(obj, key):
    return (obj.get("抽取字段") or {}).get(key, "") if obj else ""


def tax_val(inv):
    """铁路电子发票无税额，留空；其它发票 ADP 未识别时标待补充。"""
    if "铁路" in (inv.get("文档类型") or ""):
        return ""
    return clean(fld(inv, "税额")) or "待补充"


def clean(v):
    s = str(v or "").strip()
    return "" if s in ("None", "未知", "") else s


def city_match(trip_city, row_city):
    tc, rc = clean(trip_city), clean(row_city)
    if not tc or not rc:
        return False
    core = re.sub(r"[站市]$", "", tc)[:2]
    return tc in rc or (core and core in rc)


def _d(s):
    """归一成 yyyy-mm-dd 字符串；取不出返回空串。"""
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", clean(s).replace("/", "-"))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def in_range(date_str, start, end, kind=None):
    """消费日期必须落在 [出发日期, 结束日期] 内。
    机票例外：提前购票日期可能早于出差开始日期，允许放宽至出发日期前 5 天。"""
    d, s, e = _d(date_str), _d(start), _d(end)
    if not (d and s and e):
        return False
    if s <= d <= e:
        return True
    # 机票：日期略早于出差开始日也接受（提前购票），放宽至前 5 天，但必须 ≤ 结束日期
    if kind == "flight" and d < s and d <= e:
        from datetime import datetime, timedelta
        dt_d = datetime.strptime(d, "%Y-%m-%d")
        dt_s = datetime.strptime(s, "%Y-%m-%d")
        return (dt_s - dt_d).days <= 5
    return False


def associate(out_da, out_dest, start, end, consume, trips, kind=None):
    for t in trips:
        # 强匹配：开始/结束日期与申请区间完全一致（住宿场景）
        if start and end and _d(start) == _d(t.get("出发日期")) and _d(end) == _d(t.get("结束日期")):
            return str(t.get("编号", ""))
        # 城市匹配：必须叠加消费日期落在申请区间内（机票放宽 5 天），否则不同行程同城会错配
        if (city_match(t.get("出发地"), out_da) and city_match(t.get("目的地"), out_dest)
                and in_range(consume, t.get("出发日期"), t.get("结束日期"), kind)):
            return str(t.get("编号", ""))
    return ""


def build_row(g, trips, cfg, report_date):
    inv = (g.get("invoice") or {}).get("_obj")
    supp = (g.get("supplement") or {}).get("_obj")
    kind = g.get("kind")
    if inv is None:
        return None

    person = detect_person(g)  # 报销人 = 收款人 = 该行单据上的真实人名，否则「待补充」

    一级, 二级 = CAT.get(kind, ("差旅费", ""))
    if kind == "standalone":
        一级 = "差旅费"
        二级 = "火车票" if "铁路" in (inv.get("文档类型") or "") or "火车" in (inv.get("文档类型") or "") else (inv.get("二级消费类型") or "")

    # 消费日期：有补充材料取补充材料日期，否则取发票日期
    consume = clean(fld(supp, "日期")) if supp else clean(fld(inv, "日期"))

    出发地 = 目的地 = 开始 = 结束 = 招待人数 = 招待客户 = ""
    if kind in ("flight",):
        出发地, 目的地 = clean(fld(supp, "出发地")), clean(fld(supp, "目的地"))
    elif kind == "didi":
        出发地, 目的地 = clean(fld(supp, "出发地")), clean(fld(supp, "目的地"))
    elif kind == "standalone":
        出发地, 目的地 = clean(fld(inv, "出发地")), clean(fld(inv, "目的地"))
    elif kind == "hotel":
        开始, 结束 = clean(fld(supp, "入住日期")), clean(fld(supp, "结束日期"))
    elif kind == "meal":
        招待人数 = clean(fld(supp, "人数"))
        招待客户 = "待补充"

    关联 = ""
    if 一级 == "差旅费":
        关联 = associate(出发地, 目的地, 开始, 结束, consume, trips, kind)

    trip_title = ""
    for t in trips:
        if str(t.get("编号", "")) == 关联:
            trip_title = clean(t.get("标题"))
            break

    销售方 = clean(fld(inv, "销售方名称"))
    reason = gen_reason(kind, trip_title, 销售方, 出发地, 目的地, 招待人数)

    # 附件：展示用文件名 + 重命名后的真实本地路径（供第五步真正上传为飞书附件）
    attach_names, attach_paths = [], []
    for x in (g.get("invoice"), g.get("supplement")):
        if not x or not x.get("new_name"):
            continue
        attach_names.append(x["new_name"])
        if x.get("_local_path"):
            attach_paths.append(os.path.join(os.path.dirname(x["_local_path"]), x["new_name"]))
    attach = "；".join(attach_names)

    return {
        "报销人": person, "费用承担部门": cfg["费用承担部门"],
        "收款人": person, "收款账号": account_for(person, int(cfg.get("收款账号位数", 16))),
        "收款银行": cfg["收款银行"],
        "报销金额": clean(fld(inv, "价税合计")), "报销事由": reason,
        "报销日期": report_date, "附件": attach,
        "一级费用类型": 一级, "二级费用明细": 二级,
        "发票号码": clean(fld(inv, "发票号码")), "消费日期": consume,
        "金额": clean(fld(inv, "金额")), "税额": tax_val(inv),
        "报销主体": clean(fld(inv, "购买方名称")), "销售方名称": 销售方,
        "项目明细": clean(fld(inv, "项目/消费明细")),
        "出发地": 出发地, "目的地": 目的地, "开始日期": 开始, "结束日期": 结束,
        "招待人数": 招待人数, "招待客户": 招待客户, "关联出差申请编号": 关联,
        "_附件路径": attach_paths,  # 重命名后的绝对路径列表，第五步据此把文件真正上传为附件
    }


def gen_reason(kind, trip, seller, da, dest, n):
    route = f"{da}–{dest}".strip("–")
    if kind == "hotel":
        return f"{trip or '差旅'}酒店住宿费（{seller}）"
    if kind == "flight":
        return f"{trip or '差旅'}机票（{route}）"
    if kind == "didi":
        return "市内交通费（滴滴快车）"
    if kind == "meal":
        return f"业务招待餐费（{seller}{('，' + n + '人') if n else ''}）"
    if kind == "standalone":
        return f"{trip or '差旅'}火车票（{route}）"
    return "报销"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("groups_json")
    ap.add_argument("--trips")
    ap.add_argument("--date", default="<提交当日>")
    ap.add_argument("--json")
    ap.add_argument("--full", action="store_true", help="打印完整 36 列（默认只打印预览核心列）")
    a = ap.parse_args()

    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)["report"]
    with open(a.groups_json, encoding="utf-8") as f:
        groups = json.load(f)["groups"]
    trips = []
    if a.trips:
        with open(a.trips, encoding="utf-8") as f:
            trips = json.load(f)

    rows = [r for r in (build_row(g, trips, cfg, a.date) for g in groups) if r]

    # 预览（默认核心列）/ 完整（--full，36 列）
    cols = HEADERS if a.full else PREVIEW
    if not a.full:
        print(f"> 固定信息（每行相同）：费用承担部门 {cfg['费用承担部门']}"
              f" ｜ 收款银行 {cfg['收款银行']}"
              f" ｜ 收款账号 按 {cfg.get('收款账号位数', 16)} 位随机生成（演示假号）"
              f" ｜ 报销日期 {a.date}　（收款人 = 报销人，见下表「报销人」列）\n")
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    lines = ["| " + " | ".join(cols) + " |", sep]
    for r in rows:
        cells = []
        for h in cols:
            if h in BLANK:
                cells.append("")
            else:
                v = str(r.get(h, "")).replace("|", "\\|")
                cells.append(v if v else "—")
        lines.append("| " + " | ".join(cells) + " |")
    print("\n".join(lines))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"\n[build] 结构化记录写入 {a.json}（{len(rows)} 行），可交 lark-base 写入「待主管审批」视图")


if __name__ == "__main__":
    main()
