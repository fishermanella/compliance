"""播客来源采集（可配置 RSS 适配器）。

重要说明：
- PRD 未提供「那一片数据星辰」既有的可用代码与 RSS 地址，因此这里只实现可配置的 RSS 适配器，
  不发明任何 URL。未配置 rss_url 时显式返回 not_configured。
- 只收录发布时间落在过去 past_days 天内的单集；无发布时间的条目会被跳过并记录警告（无法确认时间窗）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from xml.etree import ElementTree

from .fetch import FetchError, HttpClient
from .models import CollectionResult, SourceRecord
from .schema import SOURCE_NAMES, build_event
from .util import clean_text, now_utc, parse_datetime, shorten, strip_html

MAX_FEED_BYTES = 8 * 1024 * 1024  # 防御性上限，避免超大/恶意 XML

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(xml_text: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """解析 RSS 2.0 / Atom，返回 (条目列表, feed 标题)。解析失败返回空列表。"""
    if not xml_text:
        return [], None
    if len(xml_text.encode("utf-8", errors="ignore")) > MAX_FEED_BYTES:
        raise ValueError("RSS 内容超过大小上限，已拒绝解析")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"RSS 解析失败：{exc}") from exc

    root_name = _local_name(root.tag)
    feed_title: Optional[str] = None
    items: list[dict[str, Any]] = []

    if root_name == "feed":  # Atom
        for child in root:
            if _local_name(child.tag) == "title" and feed_title is None:
                feed_title = clean_text(child.text or "")
        for entry in root.iter():
            if _local_name(entry.tag) != "entry":
                continue
            item: dict[str, Any] = {"title": "", "link": "", "published_raw": None, "description": ""}
            for node in entry:
                tag = _local_name(node.tag)
                if tag == "title":
                    item["title"] = clean_text(node.text or "")
                elif tag == "link":
                    href = node.attrib.get("href") or ""
                    rel = node.attrib.get("rel", "alternate")
                    if href and (not item["link"] or rel == "alternate"):
                        item["link"] = href.strip()
                elif tag in ("published", "updated") and not item["published_raw"]:
                    item["published_raw"] = clean_text(node.text or "")
                elif tag in ("summary", "content") and not item["description"]:
                    item["description"] = strip_html(node.text or "")
            items.append(item)
    else:  # RSS 2.0
        for channel in root:
            if _local_name(channel.tag) == "channel":
                for child in channel:
                    if _local_name(child.tag) == "title" and feed_title is None:
                        feed_title = clean_text(child.text or "")
                    if _local_name(child.tag) == "item":
                        item = {"title": "", "link": "", "published_raw": None, "description": ""}
                        for node in child:
                            tag = _local_name(node.tag)
                            if tag == "title":
                                item["title"] = clean_text(node.text or "")
                            elif tag == "link":
                                item["link"] = clean_text(node.text or "")
                            elif tag in ("pubdate", "date") and not item["published_raw"]:
                                item["published_raw"] = clean_text(node.text or "")
                            elif tag in ("description", "encoded", "summary") and not item["description"]:
                                item["description"] = strip_html(node.text or "")
                        items.append(item)
        if feed_title is None:
            for child in root:
                if _local_name(child.tag) == "title":
                    feed_title = clean_text(child.text or "")
                    break

    return items, feed_title


def filter_recent(
    items: list[dict[str, Any]],
    *,
    past_days: int,
    reference: Optional[datetime] = None,
) -> tuple[list[dict[str, Any]], int]:
    """按过去 past_days 天过滤。返回 (命中条目, 无日期条目数)。"""
    reference = reference or now_utc()
    cutoff = reference - timedelta(days=max(0, int(past_days)))
    kept: list[dict[str, Any]] = []
    undated = 0
    for item in items:
        published = parse_datetime(item.get("published_raw"))
        if published is None:
            undated += 1
            continue
        if published < cutoff or published > reference + timedelta(days=1):
            continue
        kept.append(item)
    return kept, undated


def collect(
    config: dict[str, Any],
    *,
    base_dir: Any = None,
    http: Any = None,
    raw_dir: Any = None,
    now: Optional[datetime] = None,
    browser: Any = None,
    sleep: Any = None,
) -> CollectionResult:
    """统一采集器接口：browser / sleep 对本来源不适用，仅为流水线统一调用而接受。"""
    podcast_cfg = config.get("podcast", {}) or {}
    privacy = config.get("privacy", {}) or {}
    name = str(podcast_cfg.get("name") or SOURCE_NAMES["podcast"])
    source = SourceRecord(id="podcast", name=name, status="idle", coverage_status="unknown")

    rss_url = str(podcast_cfg.get("rss_url") or "").strip()
    if not rss_url:
        source.status = "not_configured"
        source.coverage_status = "not_configured"
        source.expected_count = None
        source.error = (
            "未配置 RSS 地址：PRD 未提供既有可用代码与订阅地址，"
            "本模块仅实现可配置的 RSS 适配器，不推测 URL。"
        )
        source.warnings.append("播客来源未配置，未采集任何内容（not_configured）。")
        return CollectionResult(source=source)

    client = http if http is not None else HttpClient(
        user_agent=str(podcast_cfg.get("user_agent") or ""),
        timeout=float(podcast_cfg.get("request_timeout") or 20),
    )
    try:
        response = client.get(rss_url)
    except FetchError as exc:
        source.status = "error"
        source.coverage_status = "failed"
        source.error = str(exc)
        return CollectionResult(source=source)

    raw_pages = {rss_url: response.text}
    try:
        items, feed_title = parse_feed(response.text)
    except ValueError as exc:
        source.status = "error"
        source.coverage_status = "failed"
        source.error = str(exc)
        return CollectionResult(source=source, raw_pages=raw_pages)

    past_days = int(podcast_cfg.get("past_days") or 7)
    recent, undated = filter_recent(items, past_days=past_days, reference=now or now_utc())

    max_items = int(podcast_cfg.get("max_items") or 0)
    if max_items > 0:
        recent = recent[:max_items]

    events: list[dict[str, Any]] = []
    skipped_no_link = 0
    for item in recent:
        link = str(item.get("link") or "").strip()
        if not link:
            # 没有可用的来源 URL 就不能构成一条可追溯记录，跳过而不是编造链接
            skipped_no_link += 1
            continue
        title = str(item.get("title") or "").strip() or "播客单集（标题未解析）"
        description = shorten(str(item.get("description") or ""), int(privacy.get("max_fact_chars") or 600))
        events.append(
            build_event(
                source="podcast",
                source_url=link,
                title=title,
                collection_method="podcast_rss",
                published_date=item.get("published_raw"),
                jurisdiction="Unknown",
                authority=feed_title or name,
                event_type="Unknown",
                discovery_sources=[{"name": name, "url": link}],
                summary=shorten(description, int(privacy.get("max_quote_chars") or 300)),
                source_fact=description,
                status="Needs Review",
                relevance="needs_review",
                relevance_reason="待 AI 初判 / 人工复核",
                update_type="Unknown",
                verification_status="official_pending",
                analysis_status="skipped",
            )
        )

    source.expected_count = len(recent)
    source.collected_count = len(events)
    if not items:
        source.status = "error"
        source.coverage_status = "failed"
        source.error = "RSS 中未解析到任何条目（可能不是有效订阅源）"
    elif not events:
        source.status = "ok"
        source.coverage_status = "complete"
        source.warnings.append(f"过去 {past_days} 天内没有新单集。")
    else:
        source.status = "ok"
        source.coverage_status = "complete"

    if undated:
        source.warnings.append(
            f"{undated} 个单集缺少可解析的发布时间，已跳过（无法确认是否在 {past_days} 天窗口内，标记 date_unverified）。"
        )
    if skipped_no_link:
        source.warnings.append(f"{skipped_no_link} 个单集没有可用的链接，已跳过（不编造来源 URL）。")
    source.warnings.append(
        f"RSS 共 {len(items)} 条，窗口内 {len(recent)} 条；播客为信息线索来源，需回溯官方原文后才可视为事实。"
    )
    return CollectionResult(source=source, events=events, raw_pages=raw_pages, expected_count=len(recent))
