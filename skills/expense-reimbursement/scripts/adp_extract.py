#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADP DocFlow 文档抽取：先把票据上传到 ADP 引擎存储，再调 DocFlow 应用做字段抽取。

每个文件一次上传，所有文件合并成一次 run 调用，返回每个文件的结构化字段
（发票号码 / 价税合计 / 项目明细 / 销售方 / 申请日期 等，由 DocFlow 应用决定）。

用法:
  python3 adp_extract.py <file1> [file2 ...]
  python3 adp_extract.py --dir <目录>                # 递归抽取目录下所有 pdf/jpg/jpeg/png
  python3 adp_extract.py <files...> --out result.json

读取同 skill 目录下 ../config.json 的 adp.base_url / adp.api_key / adp.app_id。
依赖: python3, curl。
"""
import argparse
import json
import mimetypes
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.normpath(os.path.join(HERE, "..", "config.json"))
EXTS = {".pdf", ".jpg", ".jpeg", ".png"}


def load_cfg():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)["adp"]


def curl_json(args):
    p = subprocess.run(["curl", "-s", *args], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit(f"[adp] 返回非 JSON:\nstdout={p.stdout[:600]}\nstderr={p.stderr[:300]}")


def upload(cfg, path):
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    r = curl_json([
        "-X", "POST", f"{cfg['base_url']}/open/agentic_engine/laiye/files/upload",
        "-H", f"X-API-Key: {cfg['api_key']}",
        "-F", f"chunk=@{path};type={mime}",
        "-F", f"application_id={cfg['app_id']}",
        "-F", "sharing_scope=application",
    ])
    if r.get("code") != "success":
        sys.exit(f"[adp] 上传失败 {path}: {r}")
    d = r["data"]
    return {"url": d["download_url"], "name": d["file_name"],
            "mime_type": d["content_type"], "size": d["file_size"]}


def run_app(cfg, files):
    body = json.dumps({"application_id": cfg["app_id"], "files": files}, ensure_ascii=False)
    r = curl_json([
        "-X", "POST", f"{cfg['base_url']}/open/agentic_engine/laiye/v1/app/run",
        "-H", f"X-API-Key: {cfg['api_key']}",
        "-H", "Content-Type: application/json",
        "-d", body,
    ])
    if r.get("code") != "success":
        sys.exit(f"[adp] 运行失败: {r}")
    return r["data"]


def collect(paths, directory):
    found = list(paths)
    if directory:
        for root, _, fs in os.walk(directory):
            for fn in fs:
                if fn.startswith(".") or os.path.splitext(fn)[1].lower() not in EXTS:
                    continue
                found.append(os.path.join(root, fn))
    return sorted(set(os.path.abspath(p) for p in found))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--out")
    a = ap.parse_args()

    cfg = load_cfg()
    paths = collect(a.files, a.dir)
    if not paths:
        sys.exit("[adp] 没有要抽取的文件（传文件路径或 --dir 目录）")

    uploaded, path_by_name = [], {}
    for p in paths:
        u = upload(cfg, p)
        uploaded.append(u)
        path_by_name[os.path.basename(p)] = p  # 原文件名 -> 本地绝对路径，供改名用

    data = run_app(cfg, uploaded)
    results = []
    for item in data.get("output", {}).get("collected_results", []):
        obj = item.get("output_object") or {}
        obj["_local_path"] = path_by_name.get(obj.get("原文件名", ""), "")
        results.append(obj)

    payload = {"run_id": data.get("run_id"), "status": data.get("status"), "results": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[adp] {len(results)} 个文件抽取完成 -> {a.out}（run_id={data.get('run_id')}, status={data.get('status')}）")
    else:
        print(text)


if __name__ == "__main__":
    main()
