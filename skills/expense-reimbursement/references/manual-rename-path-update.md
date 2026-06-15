# 手动重命名后路径刷新

当手动编辑 `groups.json` 完成分组合并、再自己用 `mv` 改名落盘后，`extract.json` 和 `groups.json` 里的 `_local_path` 都还指向旧文件名，必须更新。

## 更新范围

两个文件都需要刷新，且 `groups.json` 每条记录有**两层** `_local_path`：
- 顶层 `_local_path`（在 `invoice._local_path` / `supplement._local_path`）
- 嵌套 `_obj._local_path`（在 `invoice._obj._local_path` / `supplement._obj._local_path`）

两层不一致会在第四步生成报销单或第六步上传附件时报文件找不到。

## 操作步骤

1. 列出改名映射（旧文件名 → 新文件名）
2. 写一个临时 Python 脚本刷新两文件（见下方模板）
3. 刷新后 `ls -la` 票据目录确认文件名与 JSON 里的 `_local_path` 一致

## Python 刷新模板

```python
import json

base = r"C:\Users\dusha\ClawWorker\<session_dir>\票据"

# 旧文件名 → 新文件名（basename only）
renames = {
    "滴滴出行行程报销单.pdf": "交通服务_滴滴出行_20260605_14.50_滴滴行程单.pdf",
    "滴滴电子发票.pdf":         "交通服务_滴滴出行_20260605_14.50_发票.pdf",
    # ... 其余映射
}

# 1. 更新 extract.json
with open(r"C:\...\extract.json", "r", encoding="utf-8") as f:
    extr = json.load(f)
for r in extr["results"]:
    fname = r["原文件名"]
    if fname in renames:
        r["_local_path"] = base + "\\" + renames[fname]
with open(r"C:\...\extract.json", "w", encoding="utf-8") as f:
    json.dump(extr, f, ensure_ascii=False, indent=2)

# 2. 更新 groups.json（两层 _local_path）
with open(r"C:\...\groups.json", "r", encoding="utf-8") as f:
    grp = json.load(f)

def update_obj(obj):
    if obj is None: return
    fname = obj.get("原文件名", "")
    if fname in renames:
        new_path = base + "\\" + renames[fname]
        obj["_local_path"] = new_path
        if "_obj" in obj and obj["_obj"]:
            obj["_obj"]["_local_path"] = new_path

for g in grp["groups"]:
    update_obj(g.get("invoice"))
    update_obj(g.get("supplement"))

with open(r"C:\...\groups.json", "w", encoding="utf-8") as f:
    json.dump(grp, f, ensure_ascii=False, indent=2)
```

> 注意：Windows 上 venv Python 的 `open()` 不识别 MSYS 路径（`/c/Users/...`），路径用 `C:\Users\...`（反斜杠）或 `C:/Users/...`（正斜杠）。
