#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨文件匹配 + 重命名。

输入：adp_extract.py 产出的 JSON（{"results": [output_object, ...]}）。
按 4 条规则把「发票」与「补充材料」配成组，算出每个文件的新名字；可选 --apply 落盘改名。

匹配规则（补充材料 ↔ 发票项目明细关键字）：
  1. 滴滴行程单   ↔ 含「客运服务费」   条件：价税合计相等 且 行程单申请日期 == 发票开票日期
  2. 酒店入住凭证 ↔ 含「住宿费」       条件：价税合计相等
  3. 机票行程单   ↔ 含「代订机票」     条件：价税合计相等
  4. 餐饮水单     ↔ 含「餐费」         条件：价税合计相等

命名规则（同组前缀一致 = 一级消费类型_销售方名称_日期_价税合计）：
  - 一级消费类型、销售方名称、价税合计 取「发票」的值（销售方以发票为准）
  - 日期取「补充材料」的指定字段：滴滴=上车时间(日期) / 酒店=入住日期 / 机票=日期 / 餐饮=日期
  - 发票文件后缀 _发票；补充材料后缀 _滴滴行程单 / _酒店入住凭证 / _机票行程单 / _餐饮水单
  - 未匹配文件（如火车票）：一级消费类型_销售方名称_日期(自身)_价税合计，无后缀

用法:
  python3 match_rename.py extract.json                 # 打印分组与改名方案
  python3 match_rename.py extract.json --out groups.json
  python3 match_rename.py extract.json --apply          # 真正执行 mv（依据 _local_path）
"""
import argparse
import json
import os
import re
import sys

# 补充材料类型 -> (发票项目明细关键字, 需要日期对齐, 命名用的补充材料日期字段, 后缀)
SUPP = {
    "didi":   {"inv_kw": "客运服务费", "date_align": True,  "name_date": "日期",     "suffix": "滴滴行程单"},
    "hotel":  {"inv_kw": "住宿费",     "date_align": False, "name_date": "入住日期", "suffix": "酒店入住凭证"},
    "flight": {"inv_kw": "代订机票",   "date_align": False, "name_date": "日期",     "suffix": "机票行程单"},
    "meal":   {"inv_kw": "餐费",       "date_align": False, "name_date": "日期",     "suffix": "餐饮水单"},
}


def supp_kind(doctype):
    """按文档类型判定补充材料种类，顺序敏感（先滴滴再通用行程单）。"""
    t = doctype or ""
    if "滴滴" in t:
        return "didi"
    if "入住" in t or "酒店" in t:
        return "hotel"
    if "水单" in t:
        return "meal"
    if "行程单" in t or "机票" in t:
        return "flight"
    return None


def is_invoice(obj):
    return "发票" in (obj.get("文档类型") or "")


def f(obj, key):
    return (obj.get("抽取字段") or {}).get(key, "")


def amount(v):
    try:
        return round(float(re.sub(r"[^\d.]", "", str(v))), 2)
    except (ValueError, TypeError):
        return None


def same_amount(a, b):
    x, y = amount(a), amount(b)
    return x is not None and y is not None and abs(x - y) < 0.005


def ymd(v):
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", str(v or ""))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else ""


def money_str(v):
    a = amount(v)
    return f"{a:.2f}" if a is not None else str(v)


def ext(path, fallback=".pdf"):
    e = os.path.splitext(path or "")[1]
    return e if e else fallback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("extract_json")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    with open(a.extract_json, encoding="utf-8") as fp:
        results = json.load(fp)["results"]

    invoices = [o for o in results if is_invoice(o)]
    supplements = [o for o in results if not is_invoice(o)]
    used_inv = set()
    groups = []

    # 先处理有补充材料的组
    for s in supplements:
        kind = supp_kind(s.get("文档类型"))
        if not kind:
            continue
        rule = SUPP[kind]
        inv = None
        for i, cand in enumerate(invoices):
            if i in used_inv:
                continue
            if rule["inv_kw"] not in f(cand, "项目/消费明细"):
                continue
            if not same_amount(f(cand, "价税合计"), f(s, "价税合计")):
                continue
            if rule["date_align"]:
                # 发票开票日期 == 行程单申请日期
                if f(s, "申请日期")[:10] != f(cand, "日期")[:10]:
                    continue
            inv = cand
            used_inv.add(i)
            break

        prefix = None
        if inv is not None:
            prefix = "_".join([
                inv.get("一级消费类型", ""),
                f(inv, "销售方名称"),
                ymd(f(s, rule["name_date"])),
                money_str(f(inv, "价税合计")),
            ])
        groups.append({
            "kind": kind,
            "matched": inv is not None,
            "prefix": prefix,
            "invoice": _file_entry(inv, prefix, "发票"),
            "supplement": _file_entry(s, prefix, rule["suffix"]) if prefix else _file_entry(s, _self_prefix(s), ""),
        })

    # 未配对的发票 = 独立项（如火车票）
    for i, inv in enumerate(invoices):
        if i in used_inv:
            continue
        prefix = _self_prefix(inv)
        groups.append({
            "kind": "standalone",
            "matched": False,
            "prefix": prefix,
            "invoice": _file_entry(inv, prefix, ""),
            "supplement": None,
        })

    out = {"groups": groups}
    text = json.dumps(out, ensure_ascii=False, indent=2)

    if a.apply:
        renamed = []
        for g in groups:
            for entry in (g["invoice"], g["supplement"]):
                if not entry or not entry.get("_local_path") or not entry.get("new_name"):
                    continue
                src = entry["_local_path"]
                dst = os.path.join(os.path.dirname(src), entry["new_name"])
                if os.path.abspath(src) != os.path.abspath(dst):
                    os.rename(src, dst)
                    renamed.append(f"{os.path.basename(src)}  ->  {entry['new_name']}")
        print("已重命名：")
        print("\n".join("  " + r for r in renamed) if renamed else "  （无）")

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fp:
            fp.write(text)
        print(f"[match] 分组写入 {a.out}（{len(groups)} 组）")
    elif not a.apply:
        print(text)


def _self_prefix(obj):
    """未匹配文件用自身字段拼前缀。"""
    if obj is None:
        return None
    return "_".join([
        obj.get("一级消费类型", ""),
        f(obj, "销售方名称"),
        ymd(f(obj, "日期")),
        money_str(f(obj, "价税合计")),
    ])


def _file_entry(obj, prefix, suffix):
    if obj is None:
        return None
    e = ext(obj.get("_local_path"), ".pdf" if "jpg" not in (obj.get("文档类型") or "") else ".jpg")
    new_name = None
    if prefix:
        new_name = f"{prefix}_{suffix}{e}" if suffix else f"{prefix}{e}"
    return {
        "原文件名": obj.get("原文件名"),
        "文档类型": obj.get("文档类型"),
        "new_name": new_name,
        "_local_path": obj.get("_local_path"),
        "_obj": obj,
    }


if __name__ == "__main__":
    main()
