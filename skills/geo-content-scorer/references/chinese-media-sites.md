# 中文媒体网站内容提取备用方案

## 问题

`fetch_url.py` 依赖 `readability-lxml` 提取正文。部分中文媒体网站（如中关村在线 ZOL、太平洋电脑网等）页面布局复杂，导航、页脚、侧栏推荐、平板报价列表等噪音与正文混在一起，readability 很难精确区分，导致提取出的文本包含大量非正文内容。

## 症状

- 提取到的文本在正文前后夹带导航文字（"网站功能 查报价 新品 排行榜..."）
- 正文后跟着几十条"参考报价：¥xxxx 去购买>"的无关列表
- 末尾混入"热门话题""论坛精选""推荐问答"等非正文区块
- `word_count` 虚高，实际正文可能只有提取量的 50-60%

## 推荐方案：浏览器 DOM 提取

当 `fetch_url.py` 返回的文本明显含大量噪音时，改用浏览器工具定位页面真实 DOM 结构：

```
1. browser_navigate(url) → 获取页面快照，定位 H1 和正文段落
2. browser_console(expression=...) → 用 JS 提取结构化段落
```

### 可靠的 JS 提取表达式

```javascript
(() => {
  const h1 = document.querySelector('h1');
  let content = '';
  if (h1) content += 'TITLE: ' + h1.textContent.trim() + '\n\n';

  const allElements = document.querySelectorAll('h1, h2, h3, p');
  let started = false;
  let stopped = false;

  for (const el of allElements) {
    const text = el.textContent.trim();
    if (!text) continue;

    // 过滤尾部噪音——按站点定制
    if (text.includes('热门话题') || text.includes('更多经销商') ||
        text.includes('论坛精选') || text.includes('下载ZOL')) {
      stopped = true;
    }
    if (stopped) continue;

    if (el === h1 || (started && el.tagName.match(/^H[1-3]$/))) {
      content += '\n## ' + text + '\n\n';
      started = true;
      continue;
    }

    if (started) {
      content += text + '\n\n';
    }
  }

  return content.trim();
})()
```

### 噪音过滤词库

按站点积累，持续扩充：

| 站点 | 噪音标记词 |
|------|-----------|
| ZOL（中关村在线） | "热门话题""更多经销商""论坛精选""参考报价""去购买>""推荐问答" |
| （待补充） | |

## 工作流建议

```
1. 先跑 fetch_url.py
2. 检查返回的 text 尾部 300 字 → 如果含大量噪音标记词，则：
   a. 启动 browser_navigate 打开原链接
   b. 用 browser_console 执行上述 JS 提取
   c. 用浏览器提取的干净文本继续评分流程
3. 保存两份文件：fetched_article.json（原始）+ article_final.txt（清洁版）
```

## 已知局限

- 浏览器方案需要页面完全加载（JS 渲染的 SPA 也能处理，比 requests 强）
- 噪音过滤词需要按站点手动维护，新站点可能漏过滤
- 页面如有反爬/人机验证，浏览器方案同样受阻
