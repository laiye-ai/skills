# 滴滴日期不一致 · 手动合并示例

## 场景

滴滴行程单上车日期（如 6/5）≠ 滴滴发票开票日期（如 6/11），`match_rename.py` 按「申请日期=开票日期」规则不会配成一组。需手动合并。

## groups.json 合并前（两个独立 group）

```json
// Group A: 未匹配的滴滴行程单 (kind=didi, matched=false, 只有 supplement)
{
  "kind": "didi",
  "matched": false,
  "prefix": null,
  "invoice": null,
  "supplement": {
    "原文件名": "滴滴出行行程报销单.pdf",
    "文档类型": "滴滴行程单",
    "new_name": "交通服务_滴滴出行_20260605_14.50.pdf",
    "_local_path": "C:\\...\\票据\\滴滴出行行程报销单.pdf",
    "_obj": { ... }
  }
}

// Group B: 独立滴滴发票 (kind=standalone, matched=false, 只有 invoice)
{
  "kind": "standalone",
  "matched": false,
  "prefix": "交通服务_滴滴出行_20260611_14.50",
  "invoice": {
    "原文件名": "滴滴电子发票.pdf",
    "文档类型": "普通电子发票",
    "new_name": "交通服务_滴滴出行_20260611_14.50.pdf",
    "_local_path": "C:\\...\\票据\\滴滴电子发票.pdf",
    "_obj": { ... }
  },
  "supplement": null
}
```

## 合并后（一个 matched group）

```json
{
  "kind": "didi",
  "matched": true,
  "prefix": "交通服务_滴滴出行_20260605_14.50",
  "invoice": {
    "原文件名": "滴滴电子发票.pdf",
    "文档类型": "普通电子发票",
    "new_name": "交通服务_滴滴出行_20260605_14.50_发票.pdf",
    "_local_path": "C:\\...\\票据\\滴滴电子发票.pdf",
    "_obj": { ... }
  },
  "supplement": {
    "原文件名": "滴滴出行行程报销单.pdf",
    "文档类型": "滴滴行程单",
    "new_name": "交通服务_滴滴出行_20260605_14.50_滴滴行程单.pdf",
    "_local_path": "C:\\...\\票据\\滴滴出行行程报销单.pdf",
    "_obj": { ... }
  }
}
```

## 关键决策

- **prefix 用行程单日期**（上车日期 6/5，不是发票开票日期 6/11）
- **new_name 统一前缀**：`_发票`、`_滴滴行程单`
- **合并后删除原来的两个独立 group**，只保留这一个 merged group
- **手动 `mv` 改名落盘** + 更新 `extract.json` 和 `groups.json` 路径（见 references/manual-rename-path-update.md）
