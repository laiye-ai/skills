# ADP 凭证失效判定与处理

## 失效信号

对 `https://adp.laiye.com/open/agentic_engine/laiye/files/upload` 发起 POST 上传，返回：

```json
{
  "code": "not_found",
  "message": "Accessor not found",
  "tips": null
}
```

HTTP 状态码为 200，所以脚本里的 `curl_json()` 会正常解析 JSON 但 `r.get("code") != "success"` 触发 `sys.exit`。

## 根因

`config.json` 里内置的 `adp.api_key` 或 `adp.app_id` 已过期/撤销。演示密钥有生命周期限制。

## 处理流程

1. 看到 `Accessor not found` 立刻停止，不要对每个文件逐个重试
2. 向用户说明 ADP 凭证失效，需要新的 `api_key` 和 `app_id`
3. 用户提供新凭证后，更新 `config.json` 的 `adp.api_key` 和 `adp.app_id`
4. 用单个小文件做一次上传测试确认连通
5. 确认通过后才继续跑全量抽取

## 不应做的

- 跳过 ADP 抽取步骤直接手工整理（除非用户明确说"没有新凭证，用手工方式继续"——见下方「手工降级方案」）
- 用 curl 测试其他端点（/run 等）——上传接口就是最直接的连通性探针
- 在不确定凭证状态时对整个票据目录跑 `adp_extract.py`（会逐个文件失败，浪费时间）

## 手工降级方案（仅限用户明确接受时使用）

当 ADP 不可用且用户接受手工方式时，从 PDF 中提取文本再手工拼 `extract.json`：

### A. 用 pymupdf 批量提取 PDF 文本

```python
import fitz, os, json

for fname in sorted(os.listdir(ticket_dir)):
    fpath = os.path.join(ticket_dir, fname)
    doc = fitz.open(fpath)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    print(f"FILE: {fname}\n{text}\n{'='*60}")
```

### B. 手工构造 extract.json

对照 PDF 提取的文本，为每个文件构造一条记录。`extract.json` 结构如下：

```json
{
  "run_id": "manual-extraction",
  "status": "completed",
  "results": [
    {
      "文档类型": "电子发票（普通发票）|酒店入住凭证|机票行程单|滴滴行程单|餐饮水单|铁路电子发票",
      "一级消费类型": "差旅费|交通费|业务招待费",
      "原文件名": "原始文件名.pdf",
      "_local_path": "C:\\Users\\...\\票据\\原始文件名.pdf",
      "抽取字段": {
        "发票号码": "...",
        "价税合计": "金额",
        "金额": "不含税金额",
        "税额": "税额",
        "日期": "YYYY-MM-DD（发票开票日期）",
        "销售方名称": "...",
        "购买方名称": "...",
        "项目/消费明细": "*住宿服务*住宿费|*经纪代理服务*代订机票|*交通运输服务*客运服务费|*生产生活服务*餐费|票价",
        "出发地": "...",
        "目的地": "...",
        "乘车人": "...",
        "旅客": "...",
        "出行人": "...",
        "入住日期": "...",
        "结束日期": "...",
        "申请日期": "...",
        "人数": "..."
      }
    }
  ]
}
```

**关键要点**：
- `文档类型` 决定了 `match_rename.py` 如何分类（发票 vs 补充材料判定靠文档类型是否含"发票"二字）
- 滴滴匹配需要行程单有 `申请日期` 字段（不是上车时间），必须和发票 `日期`（开票日期）相等
- 补充材料也需要有 `价税合计` 字段，否则匹配无法进行
- `_local_path` 用 Windows 绝对路径（`C:\Users\...`），后续脚本通过 `os.path.join` 处理

### C. 继续后续步骤

手工 extract.json 就绪后，第二步起的 `match_rename.py` 和 `build_reimbursement_table.py` 完全一样——它们只读 JSON，不关心来源是 ADP 还是手工。
