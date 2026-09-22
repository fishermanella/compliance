"""DataGuidance 来源采集。

PRD 未提供目标页面的真实超链接，因此：
- 目标 URL 默认为空，配置为空时显式返回 not_configured；
- 所有选择器（ALL 标签、条目、字段、下一页、总数）均可配置；
- 本模块从未在真实站点上验证过，代码中不声称已验证。

采集逻辑：切到 ALL 标签 → 读取总数 → 全量分页 → 对比 expected vs collected。
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin

from .fetch import HttpClient, PlaywrightFetcher, parse_with_bs4
from .models import CollectionResult, SourceRecord
from .schema import SOURCE_NAMES, build_event
from .util import clean_text, normalize_url, shorten

COUNT_PATTERN_DEFAULT = r"共\s*([0-9][0-9,]{0,9})\s*条"

SELECTOR_KEYS = (
    "all_tab",
    "expected_count",
    "item",
    "item_title",
    "item_link",
    "item_date",
    "item_authority",
    "item_jurisdiction",
    "item_type",
    "item_category",
    "next_page",
)


def selectors_of(config: dict[str, Any]) -> dict[str, str]:
    raw = (config.get("dataguidance", {}) or {}).get("selectors", {}) or {}
    return {key: str(raw.get(key) or "").strip() for key in SELECTOR_KEYS}


def selectors_configured(selectors: dict[str, str]) -> bool:
    """至少要有条目选择器与链接选择器才能解析。"""
    return bool(selectors.get("item")) and bool(selectors.get("item_link") or selectors.get("item_title"))


def extract_expected_count(text: Optional[str], pattern: str) -> Optional[int]:
    if not text:
        return None
    try:
        match = re.search(pattern or COUNT_PATTERN_DEFAULT, text)
    except re.error:
        return None
    if not match:
        return None
    digits = re.sub(r"[^0-9]", "", match.group(1))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _text_of(node: Any, selector: str) -> str:
    if node is None or not selector:
        return ""
    try:
        found = node.select_one(selector)
    except Exception:
        return ""
    if found is None:
        return ""
    return clean_text(found.get_text(" ", strip=True))


def _attr_of(node: Any, selector: str, attribute: str = "href") -> str:
    if node is None or not selector:
        return ""
    try:
        found = node.select_one(selector)
    except Exception:
        return ""
    if found is None:
        return ""
    value = found.get(attribute)
    if value is None and found.name == "a":
        value = found.get("href")
    return str(value or "").strip()


def parse_list_page(html: str, base_url: str, selectors: dict[str, str], *, count_pattern: str = "") -> dict[str, Any]:
    """解析列表页。选择器缺失时返回空条目，不猜测结构。"""
    soup = parse_with_bs4(html)
    if soup is None:
        raise RuntimeError("beautifulsoup4 未安装，无法解析 DataGuidance 页面")

    item_selector = selectors.get("item") or ""
    items: list[dict[str, Any]] = []

    if item_selector:
        try:
            nodes = soup.select(item_selector)
        except Exception:
            nodes = []
        for node in nodes:
            link = _attr_of(node, selectors.get("item_link") or "", "href")
            if not link and getattr(node, "name", "") == "a":
                link = str(node.get("href") or "").strip()
            title = _text_of(node, selectors.get("item_title") or "")
            if not title:
                title = clean_text(node.get_text(" ", strip=True))
            items.append(
                {
                    "title": shorten(title, 300),
                    "link": normalize_url(urljoin(base_url, link)) if link else "",
                    "published_raw": _text_of(node, selectors.get("item_date") or ""),
                    "authority": _text_of(node, selectors.get("item_authority") or ""),
                    "jurisdiction": _text_of(node, selectors.get("item_jurisdiction") or ""),
                    "event_type": _text_of(node, selectors.get("item_type") or ""),
                    "category": _text_of(node, selectors.get("item_category") or ""),
                }
            )

    expected: Optional[int] = None
    count_selector = selectors.get("expected_count") or ""
    if count_selector:
        try:
            node = soup.select_one(count_selector)
        except Exception:
            node = None
        if node is not None:
            expected = extract_expected_count(node.get_text(" ", strip=True), count_pattern)
    if expected is None:
        expected = extract_expected_count(soup.get_text(" ", strip=True)[:20000], count_pattern)

    next_url = ""
    next_selector = selectors.get("next_page") or ""
    if next_selector:
        try:
            node = soup.select_one(next_selector)
        except Exception:
            node = None
        if node is not None:
            href = str(node.get("href") or "").strip()
            if href and not href.startswith("#"):
                next_url = normalize_url(urljoin(base_url, href))
    if not next_url:
        try:
            rel_next = soup.select_one("a[rel='next']")
        except Exception:
            rel_next = None
        if rel_next is not None and rel_next.get("href"):
            next_url = normalize_url(urljoin(base_url, str(rel_next.get("href"))))

    return {"items": items, "next_url": next_url or None, "expected_count": expected}


def collect(
    config: dict[str, Any],
    *,
    base_dir: Any = None,
    http: Any = None,
    browser: Any = None,
    raw_dir: Any = None,
    now: Any = None,
    sleep: Any = None,
) -> CollectionResult:
    """统一采集器接口：sleep 对本来源不适用，仅为流水线统一调用而接受。"""
    dg_cfg = config.get("dataguidance", {}) or {}
    privacy = config.get("privacy", {}) or {}
    name = str(dg_cfg.get("name") or SOURCE_NAMES["dataguidance"])
    source = SourceRecord(id="dataguidance", name=name, status="idle", coverage_status="unknown")

    target_url = str(dg_cfg.get("url") or "").strip()
    selectors = selectors_of(config)
    count_pattern = str(dg_cfg.get("count_regex") or COUNT_PATTERN_DEFAULT)

    if not target_url:
        source.status = "not_configured"
        source.coverage_status = "not_configured"
        source.expected_count = None
        source.error = (
            "未配置目标 URL：PRD 未提供 DataGuidance 目标页超链接，"
            "本模块不推测地址，选择器也未经真实页面验证。"
        )
        source.warnings.append("DataGuidance 未配置，未采集任何内容（not_configured）。")
        return CollectionResult(source=source)

    if not selectors_configured(selectors):
        source.status = "error"
        source.coverage_status = "failed"
        source.error = (
            "目标 URL 已配置，但选择器未配置（至少需要 selectors.item 与 selectors.item_link）："
            "无法在未验证选择器的情况下解析页面，故不采集。"
        )
        return CollectionResult(source=source)

    engine = str(dg_cfg.get("engine") or "playwright").lower()
    max_pages = max(1, int(dg_cfg.get("max_pages") or 20))
    timeout_ms = int(dg_cfg.get("timeout_ms") or 45000)

    fetcher: Any = browser
    used_engine = engine
    if engine == "playwright":
        if fetcher is None and PlaywrightFetcher.available():
            fetcher = PlaywrightFetcher(
                user_agent=str(dg_cfg.get("user_agent") or ""),
                timeout_ms=timeout_ms,
            )
        if fetcher is None:
            used_engine = "http"
            source.warnings.append("playwright 不可用，已降级为 HTTP 抓取（可能拿不到渲染后的完整列表）。")
    else:
        used_engine = "http"

    client = http if http is not None else HttpClient(
        user_agent=str(dg_cfg.get("user_agent") or ""),
        timeout=float(dg_cfg.get("timeout_ms") or 45000) / 1000.0,
    )

    all_tab = selectors.get("all_tab") or ""
    raw_pages: dict[str, str] = {}
    collected: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    expected: Optional[int] = None
    pages_fetched = 0
    exhausted = False
    next_url: Optional[str] = target_url
    errors: list[str] = []

    while next_url and pages_fetched < max_pages:
        page_url = next_url
        html = ""
        try:
            if used_engine == "playwright" and fetcher is not None:
                if pages_fetched == 0 and all_tab and hasattr(fetcher, "fetch_with_click"):
                    html = fetcher.fetch_with_click(
                        page_url,
                        click_selector=all_tab,
                        wait_selector=selectors.get("item") or None,
                    )
                else:
                    html = fetcher.fetch(page_url, wait_selector=selectors.get("item") or None)
            else:
                html = client.get(page_url).text
                if pages_fetched == 0 and all_tab:
                    source.warnings.append(
                        "当前为 HTTP 抓取，无法点击 ALL 标签，实际统计口径可能与 ALL 全量不一致。"
                    )
        except Exception as exc:  # 网络/浏览器异常类型不统一，统一收敛为采集失败
            errors.append(f"{page_url}: {exc}")
            break

        raw_pages[page_url] = html
        pages_fetched += 1
        try:
            parsed = parse_list_page(html, page_url, selectors, count_pattern=count_pattern)
        except RuntimeError as exc:
            source.status = "error"
            source.coverage_status = "failed"
            source.error = str(exc)
            return CollectionResult(source=source, raw_pages=raw_pages)

        if expected is None and parsed["expected_count"] is not None:
            expected = parsed["expected_count"]

        for item in parsed["items"]:
            link = item.get("link") or ""
            key = link or f"{item.get('title')}|{item.get('published_raw')}"
            if key in seen_links:
                continue
            seen_links.add(key)
            collected.append(item)

        next_url = parsed.get("next_url")
        if not next_url:
            exhausted = True

    if not exhausted and pages_fetched >= max_pages:
        source.warnings.append(f"已达到 max_pages={max_pages} 上限，分页可能未跑完（覆盖率按 partial 处理）。")

    events = []
    for item in collected:
        title = str(item.get("title") or "").strip() or "DataGuidance 条目（标题未解析）"
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        category = str(item.get("category") or "").strip()
        events.append(
            build_event(
                source="dataguidance",
                source_url=link,
                title=title,
                collection_method=f"dataguidance_{used_engine}",
                published_date=item.get("published_raw"),
                jurisdiction="Unknown",
                authority=str(item.get("authority") or "") or "未知发布机构（页面未提供）",
                event_type=str(item.get("event_type") or "") or "Unknown",
                discovery_sources=[{"name": name, "url": link}],
                summary=shorten(
                    " / ".join(part for part in [category, str(item.get("event_type") or "")] if part),
                    int(privacy.get("max_quote_chars") or 300),
                ),
                source_fact="",
                topics=[category] if category else [],
                status="Needs Review",
                relevance="needs_review",
                relevance_reason="待 AI 初判 / 人工复核",
                update_type="Unknown",
                verification_status="official_pending",
                analysis_status="skipped",
            )
        )

    source.expected_count = expected
    source.collected_count = len(events)

    if not events:
        hint = "选择器未匹配到任何条目（选择器可能已过期，且未在真实站点验证）"
        source.status = "error"
        source.coverage_status = "failed"
        source.error = f"{hint}；抓取错误：{errors[0]}" if errors else hint
        return CollectionResult(source=source, raw_pages=raw_pages, expected_count=expected)

    if expected is None:
        source.status = "partial"
        source.coverage_status = "unknown"
        source.warnings.append("未能从页面解析出 ALL 标签总数，覆盖率状态为 unknown（无法判断是否全量）。")
    elif len(events) >= expected and exhausted:
        source.status = "ok"
        source.coverage_status = "complete"
    else:
        source.status = "partial"
        source.coverage_status = "partial"
        source.error = f"预期 {expected} 条，实际采集 {len(events)} 条"

    if errors:
        source.warnings.append("部分页面抓取失败：" + "；".join(errors[:3]))
    source.warnings.append(
        "DataGuidance 采集为线索来源：选择器未经真实站点验证，条目字段需人工/AI 复核后才能用于决策。"
    )
    return CollectionResult(source=source, events=events, raw_pages=raw_pages, expected_count=expected)
