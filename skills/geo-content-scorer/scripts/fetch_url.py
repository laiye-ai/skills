#!/usr/bin/env python3
"""
GEO 内容评分工具 - URL 抓取脚本

用法:
    python fetch_url.py <URL>
    python fetch_url.py <URL> --raw   # 输出原始 HTML（调试用）

输出:
    JSON 格式 stdout: {"url", "title", "text", "word_count", "error"}
    抓取失败时 error 字段会有明确原因，主流程应据此提示用户改用粘贴/文件方式。

依赖:
    pip install requests beautifulsoup4 lxml readability-lxml

依赖缺失时会自动降级为纯 requests + 简单正则提取。
"""

import sys
import json
import re
import argparse
from urllib.parse import urlparse


def fetch(url: str, raw: bool = False) -> dict:
    result = {
        "url": url,
        "title": "",
        "text": "",
        "word_count": 0,
        "error": None,
    }

    # 校验 URL
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        result["error"] = f"非法协议: {parsed.scheme}（仅支持 http/https）"
        return result

    # 尝试 import requests
    try:
        import requests
    except ImportError:
        result["error"] = "缺少依赖 requests，请运行: pip install requests beautifulsoup4 lxml readability-lxml"
        return result

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    # 抓取
    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    except requests.exceptions.Timeout:
        result["error"] = "请求超时（20s）。建议改用粘贴正文或本地文件方式。"
        return result
    except requests.exceptions.SSLError as e:
        result["error"] = f"SSL 证书错误: {e}"
        return result
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"连接失败: {e}（可能是 DNS 或防火墙阻断）"
        return result
    except Exception as e:
        result["error"] = f"抓取异常: {type(e).__name__}: {e}"
        return result

    if resp.status_code == 403:
        result["error"] = (
            "403 Forbidden（被站点反爬）。"
            "建议：在浏览器中打开链接，复制正文粘贴给我；"
            "或保存为 .md / .html 文件后让我读文件。"
        )
        return result
    if resp.status_code == 404:
        result["error"] = "404 Not Found（页面不存在）"
        return result
    if resp.status_code >= 400:
        result["error"] = f"HTTP {resp.status_code}: {resp.reason}"
        return result

    # 编码
    if resp.encoding == "ISO-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text

    if raw:
        result["text"] = html
        result["word_count"] = len(html)
        return result

    # 优先用 readability 提取正文
    title = ""
    text = ""
    try:
        from readability import Document
        doc = Document(html)
        title = (doc.title() or "").strip()
        cleaned_html = doc.summary()
        text = _html_to_text(cleaned_html)
    except ImportError:
        # 降级：用 BeautifulSoup
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            # 提取 title
            t = soup.find("title")
            if t:
                title = t.get_text(strip=True)
            else:
                h1 = soup.find("h1")
                title = h1.get_text(strip=True) if h1 else ""
            # 删除干扰节点
            for tag in soup(["script", "style", "nav", "footer", "aside", "iframe", "noscript"]):
                tag.decompose()
            # 优先找 article / main / 常见正文容器
            container = (
                soup.find("article")
                or soup.find("main")
                or soup.find(class_=re.compile(r"(article|content|post|entry|main)", re.I))
                or soup.body
                or soup
            )
            text = container.get_text("\n", strip=True)
        except ImportError:
            # 最低降级：正则去标签
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""
            text = _strip_html(html)
    except Exception as e:
        result["error"] = f"正文提取失败: {type(e).__name__}: {e}（建议改用粘贴正文）"
        return result

    # 收尾
    text = _normalize_whitespace(text)
    if len(text) < 100:
        result["error"] = (
            f"提取到的正文过短（{len(text)} 字），可能是 JS 渲染页面或反爬。"
            "建议：在浏览器中打开后复制正文粘贴。"
        )
        return result

    result["title"] = title
    result["text"] = text
    result["word_count"] = len(re.sub(r"\s+", "", text))  # 中文按字符计
    return result


def _html_to_text(html: str) -> str:
    """简单 HTML → 文本，保留段落结构"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)
    except ImportError:
        return _strip_html(html)


def _strip_html(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    return text


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main():
    p = argparse.ArgumentParser(description="抓取 URL 正文并输出 JSON")
    p.add_argument("url", help="目标 URL")
    p.add_argument("--raw", action="store_true", help="输出原始 HTML")
    args = p.parse_args()

    result = fetch(args.url, raw=args.raw)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if not result.get("error") else 1)


if __name__ == "__main__":
    main()
