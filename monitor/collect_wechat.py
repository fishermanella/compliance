"""微信公众号来源采集。

输入：data/wechat_links.txt（手工维护，允许 # 注释与空行，仅接受 https://mp.weixin.qq.com/s...）。
流程：HTTP 抓取 → 命中拦截特征则 Playwright 兜底 → 仍被拦截则标记 blocked。
约束：
- 绝不绕过验证码 / 登录 / 风控。
- 解析不到发布日期时 published_date 为 null，并显式记录 date_unverified 警告。
- 原始 HTML 只写入 data/raw（私有），对外仅发布短引文与摘要。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from .fetch import FetchError, HttpClient, PlaywrightFetcher, detect_block, parse_with_bs4
from .models import CollectionResult, SourceRecord
from .schema import SOURCE_NAMES, build_event
from .util import (
    clean_text,
    hash_id,
    normalize_url,
    shorten,
    strip_html,
)

COMMENT_PREFIX = "#"
DATE_UNVERIFIED = "date_unverified"

WECHAT_HOST = "mp.weixin.qq.com"


# --------------------------------------------------------------------------
# 链接文件解析 / 校验 / 合并（供 CLI 与 POST /api/links 共用）
# --------------------------------------------------------------------------

@dataclass
class LinkEntry:
    kind: str  # 'url' | 'comment' | 'blank'
    raw: str
    url: str = ""

    @property
    def is_url(self) -> bool:
        return self.kind == "url"


def parse_links_text(text: Optional[str]) -> list[LinkEntry]:
    entries: list[LinkEntry] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            entries.append(LinkEntry(kind="blank", raw=line))
        elif stripped.startswith(COMMENT_PREFIX):
            entries.append(LinkEntry(kind="comment", raw=line))
        else:
            entries.append(LinkEntry(kind="url", raw=line, url=stripped))
    return entries


def validate_url_entry(raw: str) -> tuple[bool, str, str]:
    """返回 (是否合法, 规范化 URL, 失败原因)。"""
    value = (raw or "").strip()
    if not value:
        return False, "", "空行"
    try:
        parts = urlsplit(value)
    except ValueError:
        return False, "", "URL 无法解析"
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, "", "缺少 http/https 协议"
    if scheme != "https":
        return False, "", "仅接受 https 链接"
    host = (parts.hostname or "").lower()
    if host != WECHAT_HOST:
        return False, "", f"仅接受 {WECHAT_HOST} 域名"
    path = parts.path or ""
    if not (path == "/s" or path.startswith("/s/")):
        return False, "", "仅接受 /s 形式的公众号文章链接"
    return True, normalize_url(value), ""


def extract_urls(text: Optional[str]) -> list[str]:
    return [entry.url for entry in parse_links_text(text) if entry.is_url]


def merge_links_text(existing_text: Optional[str], incoming_text: Optional[str]) -> tuple[str, int, list[dict[str, str]]]:
    """合并新链接。

    返回 (新文本, 新增数量, 非法条目列表)。
    只要存在非法条目就完全不写入（保持原有内容不变）。
    """
    existing = existing_text or ""
    invalid: list[dict[str, str]] = []
    valid: list[str] = []
    for entry in parse_links_text(incoming_text):
        if not entry.is_url:
            continue
        ok, url, reason = validate_url_entry(entry.raw)
        if ok:
            valid.append(url)
        else:
            invalid.append({"value": entry.raw.strip(), "reason": reason})

    if invalid:
        return existing, 0, invalid

    existing_keys = {normalize_url(u) for u in extract_urls(existing)}
    added: list[str] = []
    for url in valid:
        key = normalize_url(url)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        added.append(key)

    if not added:
        return existing, 0, []

    merged = existing
    if merged and not merged.endswith("\n"):
        merged += "\n"
    merged += "\n".join(added) + "\n"
    return merged, len(added), []


def read_links_file(path: Path | str) -> tuple[str, list[str]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return "", []
    return text, extract_urls(text)


# --------------------------------------------------------------------------
# 文章解析
# --------------------------------------------------------------------------

_AUTHOR_META_RES = (
    r'<meta[^>]+property=["\']og:article:author["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
)
_PUBLISHED_META_RES = (
    r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
)

_CREATE_TIME_RE = re.compile(r"var\s+create_time\s*=\s*[\"']?(\d{9,13})")
_PUBLISH_TIME_RE = re.compile(r'id=["\']publish_time["\'][^>]*>([^<]{4,40})<')
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_JS_CONTENT_RE = re.compile(
    r'<div[^>]+id=["\']js_content["\'][^>]*>(.*?)</div>\s*(?:<script|<div[^>]+id=["\']js_)',
    re.IGNORECASE | re.DOTALL,
)


def parse_wechat_article(html: str, url: str) -> dict[str, Any]:
    """解析标题 / 公众号 / 发布时间 / 正文文本 / 外链。缺失字段一律为 None 或空串。"""
    soup = parse_with_bs4(html)
    result: dict[str, Any] = {
        "title": "",
        "account": "",
        "published_raw": None,
        "text": "",
        "links": [],
        "date_unverified": False,
    }

    if soup is not None:
        title_node = soup.select_one("meta[property='og:title']")
        if title_node and title_node.get("content"):
            result["title"] = clean_text(title_node["content"])
        if not result["title"]:
            node = soup.select_one("#activity-name")
            if node:
                result["title"] = clean_text(node.get_text(" ", strip=True))

        author_node = soup.select_one("meta[property='og:article:author']") or soup.select_one("meta[name='author']")
        if author_node and author_node.get("content"):
            result["account"] = clean_text(author_node["content"])
        if not result["account"]:
            node = soup.select_one("#js_name")
            if node:
                result["account"] = clean_text(node.get_text(" ", strip=True))

        published_node = soup.select_one("meta[property='article:published_time']")
        if published_node and published_node.get("content"):
            result["published_raw"] = clean_text(published_node["content"])
        if not result["published_raw"]:
            node = soup.select_one("#publish_time")
            if node:
                result["published_raw"] = clean_text(node.get_text(" ", strip=True))

        content_node = soup.select_one("#js_content")
        if content_node is not None:
            result["text"] = clean_text(content_node.get_text("\n", strip=True))
            links: list[str] = []
            for anchor in content_node.find_all("a", href=True):
                href = urljoin(url, str(anchor["href"]).strip())
                if href.startswith("http") and href not in links:
                    links.append(href)
            result["links"] = links

    if not result["title"]:
        match = _TITLE_TAG_RE.search(html or "")
        if match:
            result["title"] = clean_text(strip_html(match.group(1)))
    if not result["account"]:
        for pattern in _AUTHOR_META_RES:
            match = re.search(pattern, html or "", re.IGNORECASE)
            if match:
                result["account"] = clean_text(match.group(1))
                break
    if not result["published_raw"]:
        for pattern in _PUBLISHED_META_RES:
            match = re.search(pattern, html or "", re.IGNORECASE)
            if match:
                result["published_raw"] = clean_text(match.group(1))
                break
    if not result["published_raw"]:
        match = _CREATE_TIME_RE.search(html or "")
        if match:
            result["published_raw"] = match.group(1)
        else:
            match = _PUBLISH_TIME_RE.search(html or "")
            if match:
                result["published_raw"] = clean_text(match.group(1))
    if not result["text"]:
        match = _JS_CONTENT_RE.search(html or "")
        if match:
            result["text"] = strip_html(match.group(1))
    if not result["links"]:
        for href in re.findall(r'href=["\'](https?://[^"\']+)["\']', html or ""):
            if href not in result["links"]:
                result["links"].append(href)

    if not result["published_raw"]:
        result["date_unverified"] = True

    result["title"] = shorten(result["title"], 200)
    result["account"] = shorten(result["account"], 120)
    return result


# --------------------------------------------------------------------------
# 采集主流程
# --------------------------------------------------------------------------

def collect(
    config: dict[str, Any],
    *,
    base_dir: Path | str,
    http: Any = None,
    browser: Any = None,
    raw_dir: Optional[Path | str] = None,
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionResult:
    wechat_cfg = config.get("wechat", {}) or {}
    privacy = config.get("privacy", {}) or {}
    name = str(wechat_cfg.get("name") or SOURCE_NAMES["wechat"])
    source = SourceRecord(id="wechat", name=name, status="idle", coverage_status="unknown")

    links_path = Path(base_dir) / str(wechat_cfg.get("links_file") or "data/wechat_links.txt")
    if not links_path.is_file():
        source.status = "error"
        source.coverage_status = "failed"
        source.error = f"链接文件不存在：{links_path}"
        return CollectionResult(source=source)

    _, urls = read_links_file(links_path)
    expected = len(urls)
    source.expected_count = expected
    if expected == 0:
        source.status = "ok"
        source.coverage_status = "complete"
        source.collected_count = 0
        source.warnings.append("data/wechat_links.txt 中暂无有效文章链接，未采集任何内容。")
        return CollectionResult(source=source)

    client = http if http is not None else HttpClient(
        user_agent=str(wechat_cfg.get("user_agent") or ""),
        timeout=float(wechat_cfg.get("request_timeout") or 20),
    )
    use_fallback = bool(wechat_cfg.get("use_playwright_fallback", True))
    playwright_fetcher = browser
    if playwright_fetcher is None and use_fallback and PlaywrightFetcher.available():
        playwright_fetcher = PlaywrightFetcher(
            user_agent=str(wechat_cfg.get("user_agent") or ""),
            timeout_ms=int(wechat_cfg.get("playwright_timeout_ms") or 30000),
        )

    max_items = int(wechat_cfg.get("max_items") or 0)
    targets = urls[:max_items] if max_items > 0 else urls
    delay = float(wechat_cfg.get("min_delay_seconds") or 0)

    events: list[dict[str, Any]] = []
    raw_pages: dict[str, str] = {}
    failures: list[str] = []
    blocked: list[str] = []
    date_missing: list[str] = []

    for index, url in enumerate(targets):
        if index and delay:
            sleep(delay)
        html = ""
        method = "wechat_html"
        block_marker: Optional[str] = None
        try:
            response = client.get(url)
            html = response.text
            block_marker = detect_block(html)
        except FetchError as exc:
            failures.append(f"{url}: {exc}")

        if block_marker and playwright_fetcher is not None:
            method = "wechat_playwright_fallback"
            try:
                html = playwright_fetcher.fetch(url)
                block_marker = detect_block(html)
            except Exception as exc:  # 浏览器失败不致命
                failures.append(f"{url}: Playwright 兜底失败 {exc}")

        if block_marker:
            blocked.append(f"{url}: 命中拦截特征「{block_marker}」")
            continue

        if not html:
            if not any(url in item for item in failures):
                failures.append(f"{url}: 未获取到页面内容")
            continue

        raw_pages[url] = html
        article = parse_wechat_article(html, url)
        if article["date_unverified"]:
            date_missing.append(url)

        content_text = article["text"] or ""
        quote = shorten(content_text, int(privacy.get("max_quote_chars") or 300))
        fact = shorten(content_text, int(privacy.get("max_fact_chars") or 600))
        title = article["title"] or f"微信公众号文章（标题未解析） {hash_id(url, length=6)}"

        events.append(
            build_event(
                source="wechat",
                source_url=url,
                title=title,
                collection_method=method,
                published_date=article["published_raw"],
                jurisdiction="CN",
                authority=article["account"] or "未知公众号（原文未标注）",
                event_type="Unknown",
                discovery_sources=[{"name": name, "url": url}],
                summary=quote,
                source_fact=fact,
                status="Needs Review",
                relevance="needs_review",
                relevance_reason="待 AI 初判 / 人工复核",
                update_type="Unknown",
                verification_status="official_pending",
                analysis_status="skipped",
            )
        )

    source.collected_count = len(events)
    if not events:
        source.status = "blocked" if blocked else "error"
        source.coverage_status = "failed"
        source.error = (blocked + failures)[0] if (blocked or failures) else "未采集到任何文章"
    elif len(events) < expected:
        source.status = "partial"
        source.coverage_status = "partial"
        source.error = f"{expected - len(events)} 条链接未采集成功"
    else:
        source.status = "ok"
        source.coverage_status = "complete"

    if blocked:
        source.warnings.append(
            "以下链接被平台拦截，已停止处理（不尝试绕过验证码/登录）：" + "；".join(blocked[:5])
        )
    if failures:
        source.warnings.append("抓取失败：" + "；".join(failures[:5]))
    if date_missing:
        source.warnings.append(
            f"{len(date_missing)} 条文章未解析到发布日期，published_date 记为 null（{DATE_UNVERIFIED}）。"
        )

    return CollectionResult(source=source, events=events, raw_pages=raw_pages, expected_count=expected)
