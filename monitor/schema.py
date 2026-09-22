"""对外 payload 的 schema、枚举校验、事件构造与去重合并。

契约要点：
- 任何对外输出都必须通过 validate_payload()。
- 来源日期 / URL 一律来自采集结果，绝不由模型发明。
- 真实数据与演示数据严格隔离（is_demo 必须与 payload.mode 一致）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from .util import (
    dedupe_key,
    format_date,
    hash_id,
    is_authority_url,
    iso_week,
    iso_now,
    normalize_url,
    now_utc,
    shorten,
    url_host,
    week_bounds,
)

MODES = ("live", "demo")

SOURCE_IDS = ("dataguidance", "wechat", "podcast")

SOURCE_NAMES = {
    "dataguidance": "DataGuidance",
    "wechat": "微信公众号",
    "podcast": "播客 · 那一片数据星辰",
}

SOURCE_STATUSES = ("ok", "partial", "error", "blocked", "not_configured", "idle")
COVERAGE_STATUSES = ("complete", "partial", "unknown", "not_configured", "failed")

JURISDICTIONS = ("CN", "EU", "US", "US-CA", "JP", "KR", "Unknown")
RELEVANCE_LEVELS = ("high", "medium", "low", "irrelevant", "needs_review")
EVENT_STATUSES = ("New", "Updated", "Effective", "Background", "Needs Review")
VERIFICATION_STATUSES = ("verified", "official_pending", "official_retrieved")
ANALYSIS_STATUSES = ("ok", "needs_review", "error", "skipped")

# 仅作提示，不作为强枚举（契约中 event_type / update_type 为自由字符串）
SUGGESTED_EVENT_TYPES = (
    "Regulation",
    "Guideline",
    "Standard",
    "Enforcement",
    "Consultation",
    "Notice",
    "Other",
    "Unknown",
)
SUGGESTED_UPDATE_TYPES = ("New", "Amendment", "Guidance", "Enforcement", "Consultation", "Other", "Unknown")

SOURCE_ID_PREFIX = {"dataguidance": "dg", "wechat": "wx", "podcast": "pod"}

RELEVANCE_RANK = {"needs_review": 0, "irrelevant": 1, "low": 2, "medium": 3, "high": 4}
VERIFICATION_RANK = {"official_pending": 1, "official_retrieved": 2, "verified": 3}

EVENT_FIELDS = (
    "event_id",
    "source",
    "collection_method",
    "title",
    "published_date",
    "jurisdiction",
    "authority",
    "event_type",
    "source_url",
    "discovery_sources",
    "summary",
    "relevance",
    "relevance_reason",
    "topics",
    "update_type",
    "is_substantive",
    "status",
    "effective_date",
    "official_url",
    "verification_status",
    "source_fact",
    "ai_analysis",
    "analysis_status",
    "is_demo",
)

SOURCE_FIELDS = ("id", "name", "status", "coverage_status", "collected_count", "expected_count", "error")

PAYLOAD_FIELDS = ("mode", "generated_at", "period", "stats", "sources", "events", "highlights", "warnings")


# --------------------------------------------------------------------------
# 枚举强制
# --------------------------------------------------------------------------

def coerce_enum(
    value: Any,
    allowed: Iterable[str],
    default: str,
    field: str,
    problems: Optional[list[str]] = None,
) -> str:
    allowed = tuple(allowed)
    if isinstance(value, str):
        candidate = value.strip()
        for option in allowed:
            if candidate.lower() == option.lower():
                return option
    if problems is not None:
        problems.append(f"{field}: 非法枚举值 {value!r}，已降级为 {default!r}")
    return default


def coerce_bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True
        if lowered in ("false", "no", "n", "0"):
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return None


def coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return "、".join(coerce_str(v) for v in value if v not in (None, ""))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ";").replace("、", ";").replace(",", ";").split(";")]
        return [p for p in parts if p]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = coerce_str(item)
            if text and text not in out:
                out.append(text)
        return out
    text = coerce_str(value)
    return [text] if text else []


def coerce_date(value: Any) -> Optional[str]:
    """只接受能被明确解析的日期，否则 None（绝不发明日期）。"""
    return format_date(value)


def make_event_id(source: str, source_url: str, title: str) -> str:
    prefix = SOURCE_ID_PREFIX.get(source, "ev")
    key = dedupe_key(source_url) or normalize_url(source_url) or title
    return f"{prefix}-{hash_id(source, key, title)}"


# --------------------------------------------------------------------------
# 事件构造
# --------------------------------------------------------------------------

def build_event(
    *,
    source: str,
    source_url: str,
    title: str,
    collection_method: str = "",
    published_date: Any = None,
    jurisdiction: Any = "Unknown",
    authority: str = "",
    event_type: str = "Unknown",
    discovery_sources: Optional[list[dict[str, str]]] = None,
    summary: str = "",
    source_fact: str = "",
    effective_date: Any = None,
    topics: Any = None,
    update_type: str = "Unknown",
    is_substantive: Any = None,
    status: Any = "Background",
    relevance: Any = "needs_review",
    relevance_reason: str = "",
    official_url: Optional[str] = None,
    verification_status: Any = "official_pending",
    ai_analysis: str = "",
    analysis_status: Any = "skipped",
    is_demo: bool = False,
    event_id: Optional[str] = None,
    problems: Optional[list[str]] = None,
) -> dict[str, Any]:
    """构造符合契约的事件 dict。非法枚举降级并记录问题，不静默通过。"""
    issues: list[str] = problems if problems is not None else []
    url = normalize_url(source_url)

    discovery = []
    for item in discovery_sources or []:
        if not isinstance(item, dict):
            continue
        name = coerce_str(item.get("name"))
        link = normalize_url(coerce_str(item.get("url")))
        entry = {"name": name, "url": link}
        if entry not in discovery:
            discovery.append(entry)
    if not discovery and url:
        discovery = [{"name": SOURCE_NAMES.get(source, source), "url": url}]

    event = {
        "event_id": event_id or make_event_id(source, url or source_url, title),
        "source": source,
        "collection_method": coerce_str(collection_method),
        "title": coerce_str(title),
        "published_date": coerce_date(published_date),
        "jurisdiction": coerce_enum(jurisdiction, JURISDICTIONS, "Unknown", "jurisdiction", issues),
        "authority": coerce_str(authority),
        "event_type": coerce_str(event_type, "Unknown") or "Unknown",
        "source_url": url,
        "discovery_sources": discovery,
        "summary": coerce_str(summary),
        "relevance": coerce_enum(relevance, RELEVANCE_LEVELS, "needs_review", "relevance", issues),
        "relevance_reason": coerce_str(relevance_reason),
        "topics": coerce_str_list(topics),
        "update_type": coerce_str(update_type, "Unknown") or "Unknown",
        "is_substantive": coerce_bool_or_none(is_substantive),
        "status": coerce_enum(status, EVENT_STATUSES, "Needs Review", "status", issues),
        "effective_date": coerce_date(effective_date),
        "official_url": normalize_url(official_url) or None,
        "verification_status": coerce_enum(
            verification_status, VERIFICATION_STATUSES, "official_pending", "verification_status", issues
        ),
        "source_fact": coerce_str(source_fact),
        "ai_analysis": coerce_str(ai_analysis),
        "analysis_status": coerce_enum(analysis_status, ANALYSIS_STATUSES, "skipped", "analysis_status", issues),
        "is_demo": bool(is_demo),
    }
    return event


# --------------------------------------------------------------------------
# 去重 / 合并
# --------------------------------------------------------------------------

def _merge_scalar(primary: dict[str, Any], other: dict[str, Any], field: str) -> Any:
    current = primary.get(field)
    candidate = other.get(field)
    if isinstance(current, str):
        if not current.strip() and isinstance(candidate, str) and candidate.strip():
            return candidate
        return current
    if current in (None, [], {}):
        return candidate if candidate not in (None, [], {}) else current
    return current


def _merge_events(primary: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)

    for field in ("collection_method", "title", "authority", "event_type", "update_type",
                  "summary", "source_fact", "ai_analysis", "relevance_reason",
                  "published_date", "effective_date", "official_url"):
        merged[field] = _merge_scalar(primary, other, field)

    if not merged.get("summary"):
        merged["summary"] = shorten(other.get("source_fact") or other.get("summary") or "", 200)

    # 相关性取更高等级（needs_review 最低）
    left = RELEVANCE_RANK.get(primary.get("relevance", "needs_review"), 0)
    right = RELEVANCE_RANK.get(other.get("relevance", "needs_review"), 0)
    merged["relevance"] = primary.get("relevance") if left >= right else other.get("relevance")

    # 核验状态取更高等级
    left_v = VERIFICATION_RANK.get(primary.get("verification_status", "official_pending"), 0)
    right_v = VERIFICATION_RANK.get(other.get("verification_status", "official_pending"), 0)
    merged["verification_status"] = (
        primary.get("verification_status") if left_v >= right_v else other.get("verification_status")
    )

    # 分析状态：ok 优先
    if other.get("analysis_status") == "ok" and primary.get("analysis_status") != "ok":
        merged["analysis_status"] = "ok"
        if not merged.get("ai_analysis"):
            merged["ai_analysis"] = other.get("ai_analysis", "")

    # 状态：非 Needs Review 优先
    if primary.get("status") == "Needs Review" and other.get("status") != "Needs Review":
        merged["status"] = other.get("status")

    # is_substantive：任一为真即真；全为假则为假；否则 None
    flags = [primary.get("is_substantive"), other.get("is_substantive")]
    if any(flag is True for flag in flags):
        merged["is_substantive"] = True
    elif all(flag is False for flag in flags if flag is not None) and any(flag is False for flag in flags):
        merged["is_substantive"] = False
    else:
        merged["is_substantive"] = None

    topics = list(primary.get("topics") or [])
    for topic in other.get("topics") or []:
        if topic not in topics:
            topics.append(topic)
    merged["topics"] = topics

    discovery = list(primary.get("discovery_sources") or [])
    for item in other.get("discovery_sources") or []:
        if item not in discovery:
            discovery.append(item)
    merged["discovery_sources"] = discovery

    merged["is_demo"] = bool(primary.get("is_demo") or other.get("is_demo"))
    return merged


def _canonical_key(event: dict[str, Any], domains: tuple[str, ...]) -> str:
    """跨来源合并用的规范键：仅当 URL 命中权威域名白名单时才生成（安全前提）。"""
    if not domains:
        return ""
    for candidate in (event.get("official_url"), event.get("source_url")):
        if candidate and is_authority_url(candidate, domains):
            return dedupe_key(candidate)
    return ""


def dedupe_events(
    events: list[dict[str, Any]],
    *,
    authority_domains: Optional[Iterable[str]] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """先按规范化 URL 去重，再在安全前提下按权威官方规范 URL 跨来源合并。"""
    warnings: list[str] = []
    if not events:
        return [], warnings

    # 第一轮：同源（规范化 URL 相同）
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        key = dedupe_key(event.get("source_url")) or event.get("event_id") or ""
        if not key:
            key = f"no-url:{event.get('event_id')}"
        if key in buckets:
            buckets[key] = _merge_events(buckets[key], event)
        else:
            buckets[key] = dict(event)
            order.append(key)

    stage_one = [buckets[key] for key in order]

    # 第二轮：跨来源，仅在 URL 命中权威域名时合并（安全前提）
    domains = tuple(authority_domains or ())
    if domains:
        canonical: dict[str, int] = {}
        merged_count = 0
        output: list[dict[str, Any]] = []
        for event in stage_one:
            key = _canonical_key(event, domains)
            if key and key in canonical:
                target = canonical[key]
                output[target] = _merge_events(output[target], event)
                merged_count += 1
                continue
            if key:
                canonical[key] = len(output)
            output.append(event)
        if merged_count:
            warnings.append(
                f"已按权威官方规范 URL 合并 {merged_count} 条跨来源记录（仅限配置的权威域名，未做事实核验）。"
            )
        stage_one = output

    dropped = len(events) - len(stage_one)
    if dropped > 0:
        warnings.append(f"去重合并：原始 {len(events)} 条 → {len(stage_one)} 条（合并 {dropped} 条）。")
    return stage_one, warnings


def compute_stats(events: list[dict[str, Any]]) -> dict[str, int]:
    monitored = len(events)
    relevant = sum(1 for e in events if e.get("relevance") in ("high", "medium"))
    high = sum(1 for e in events if e.get("relevance") == "high")
    verified = sum(1 for e in events if e.get("verification_status") == "verified")
    return {"monitored": monitored, "relevant": relevant, "high": high, "verified": verified}


def build_period(moment: Optional[datetime] = None) -> dict[str, str]:
    moment = moment or now_utc()
    start, end = week_bounds(moment)
    return {"start": start, "end": end, "week": iso_week(moment)}


def build_payload(
    *,
    mode: str,
    sources: list[dict[str, Any]],
    events: list[dict[str, Any]],
    highlights: Optional[list[str]] = None,
    warnings: Optional[list[str]] = None,
    generated_at: Optional[str] = None,
    period: Optional[dict[str, str]] = None,
    moment: Optional[datetime] = None,
) -> dict[str, Any]:
    mode = mode if mode in MODES else "live"
    events = sorted(
        events,
        key=lambda e: (
            -(RELEVANCE_RANK.get(e.get("relevance", "needs_review"), 0)),
            e.get("published_date") or "",
            e.get("event_id") or "",
        ),
    )
    for event in events:
        event["is_demo"] = mode == "demo"
    return {
        "mode": mode,
        "generated_at": generated_at,
        "period": period or build_period(moment),
        "stats": compute_stats(events),
        "sources": sources,
        "events": events,
        "highlights": list(highlights or []),
        "warnings": list(warnings or []),
    }


def empty_live_payload(configured: Optional[dict[str, bool]] = None, moment: Optional[datetime] = None) -> dict[str, Any]:
    """尚未产生真实数据时的空 payload（不是演示数据）。"""
    configured = configured or {}
    sources = []
    for source_id in SOURCE_IDS:
        is_configured = bool(configured.get(source_id))
        sources.append(
            {
                "id": source_id,
                "name": SOURCE_NAMES[source_id],
                "status": "idle" if is_configured else "not_configured",
                "coverage_status": "unknown" if is_configured else "not_configured",
                "collected_count": 0,
                "expected_count": None,
                "error": None if is_configured else "尚未配置数据源",
            }
        )
    return {
        "mode": "live",
        "generated_at": None,
        "period": build_period(moment),
        "stats": {"monitored": 0, "relevant": 0, "high": 0, "verified": 0},
        "sources": sources,
        "events": [],
        "highlights": [],
        "warnings": ["尚未执行任何真实监测运行，当前为空的实时数据占位（非演示数据）。"],
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def _check_enum(value: Any, allowed: Iterable[str], path: str, problems: list[str]) -> None:
    if value not in tuple(allowed):
        problems.append(f"{path}: 值 {value!r} 不在允许集合 {tuple(allowed)}")


def validate_payload(payload: Any) -> list[str]:
    """返回问题列表；空列表表示通过。"""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["payload 不是对象"]

    for field in PAYLOAD_FIELDS:
        if field not in payload:
            problems.append(f"payload 缺少字段 {field}")
    if problems:
        return problems

    mode = payload.get("mode")
    _check_enum(mode, MODES, "mode", problems)

    generated_at = payload.get("generated_at")
    if generated_at is not None and not isinstance(generated_at, str):
        problems.append("generated_at 必须是 ISO 字符串或 null")

    period = payload.get("period")
    if not isinstance(period, dict) or set(period) != {"start", "end", "week"}:
        problems.append("period 必须包含 start/end/week")
    else:
        for key in ("start", "end"):
            value = period.get(key)
            if not isinstance(value, str) or format_date(value) != value:
                problems.append(f"period.{key} 必须是 YYYY-MM-DD")
        if not isinstance(period.get("week"), str):
            problems.append("period.week 必须是字符串")

    stats = payload.get("stats")
    if not isinstance(stats, dict) or set(stats) != {"monitored", "relevant", "high", "verified"}:
        problems.append("stats 必须包含 monitored/relevant/high/verified")
    else:
        for key, value in stats.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"stats.{key} 必须是非负整数")

    sources = payload.get("sources")
    if not isinstance(sources, list):
        problems.append("sources 必须是数组")
    else:
        seen_ids = set()
        for index, source in enumerate(sources):
            path = f"sources[{index}]"
            if not isinstance(source, dict):
                problems.append(f"{path} 不是对象")
                continue
            for field in SOURCE_FIELDS:
                if field not in source:
                    problems.append(f"{path} 缺少字段 {field}")
            _check_enum(source.get("id"), SOURCE_IDS, f"{path}.id", problems)
            if source.get("id") in seen_ids:
                problems.append(f"{path}.id 重复：{source.get('id')}")
            seen_ids.add(source.get("id"))
            _check_enum(source.get("status"), SOURCE_STATUSES, f"{path}.status", problems)
            _check_enum(source.get("coverage_status"), COVERAGE_STATUSES, f"{path}.coverage_status", problems)
            if not isinstance(source.get("name"), str) or not source.get("name"):
                problems.append(f"{path}.name 必须是非空字符串")
            for field in ("collected_count",):
                value = source.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    problems.append(f"{path}.{field} 必须是非负整数")
            expected = source.get("expected_count")
            if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool) or expected < 0):
                problems.append(f"{path}.expected_count 必须是 null 或非负整数")
            if source.get("error") is not None and not isinstance(source.get("error"), str):
                problems.append(f"{path}.error 必须是 null 或字符串")

    events = payload.get("events")
    if not isinstance(events, list):
        problems.append("events 必须是数组")
    else:
        ids = set()
        for index, event in enumerate(events):
            problems.extend(_validate_event(event, f"events[{index}]", mode))
            if isinstance(event, dict):
                event_id = event.get("event_id")
                if event_id in ids:
                    problems.append(f"events[{index}].event_id 重复：{event_id}")
                ids.add(event_id)
        if isinstance(sources, list):
            known_sources = {s.get("id") for s in sources if isinstance(s, dict)}
            for index, event in enumerate(events):
                if isinstance(event, dict) and event.get("source") not in known_sources:
                    problems.append(f"events[{index}].source 未在 sources 中声明：{event.get('source')}")

    highlights = payload.get("highlights")
    if not isinstance(highlights, list) or any(not isinstance(h, str) for h in highlights):
        problems.append("highlights 必须是字符串数组")
    elif isinstance(events, list):
        known = {e.get("event_id") for e in events if isinstance(e, dict)}
        for item in highlights:
            if item not in known:
                problems.append(f"highlights 引用了不存在的事件：{item}")

    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        problems.append("warnings 必须是字符串数组")

    return problems


def _validate_event(event: Any, path: str, mode: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(event, dict):
        return [f"{path} 不是对象"]
    for field in EVENT_FIELDS:
        if field not in event:
            problems.append(f"{path} 缺少字段 {field}")
    if problems:
        return problems

    if not isinstance(event.get("event_id"), str) or not event.get("event_id"):
        problems.append(f"{path}.event_id 必须是非空字符串")
    _check_enum(event.get("source"), SOURCE_IDS, f"{path}.source", problems)
    if not isinstance(event.get("title"), str) or not event.get("title"):
        problems.append(f"{path}.title 必须是非空字符串")
    _check_enum(event.get("jurisdiction"), JURISDICTIONS, f"{path}.jurisdiction", problems)
    _check_enum(event.get("relevance"), RELEVANCE_LEVELS, f"{path}.relevance", problems)
    _check_enum(event.get("status"), EVENT_STATUSES, f"{path}.status", problems)
    _check_enum(event.get("verification_status"), VERIFICATION_STATUSES, f"{path}.verification_status", problems)
    _check_enum(event.get("analysis_status"), ANALYSIS_STATUSES, f"{path}.analysis_status", problems)

    for field in ("published_date", "effective_date"):
        value = event.get(field)
        if value is not None and (not isinstance(value, str) or format_date(value) != value):
            problems.append(f"{path}.{field} 必须是 null 或 YYYY-MM-DD")

    if not isinstance(event.get("is_substantive"), (bool, type(None))):
        problems.append(f"{path}.is_substantive 必须是 null 或布尔值")

    if not isinstance(event.get("topics"), list) or any(not isinstance(t, str) for t in event.get("topics") or []):
        problems.append(f"{path}.topics 必须是字符串数组")

    discovery = event.get("discovery_sources")
    if not isinstance(discovery, list):
        problems.append(f"{path}.discovery_sources 必须是数组")
    else:
        for j, item in enumerate(discovery):
            if not isinstance(item, dict) or set(item) != {"name", "url"}:
                problems.append(f"{path}.discovery_sources[{j}] 必须包含 name/url")
                continue
            if not isinstance(item.get("name"), str) or not isinstance(item.get("url"), str):
                problems.append(f"{path}.discovery_sources[{j}] name/url 必须是字符串")

    if not isinstance(event.get("source_url"), str) or not event.get("source_url"):
        problems.append(f"{path}.source_url 必须是非空字符串")
    official = event.get("official_url")
    if official is not None and not isinstance(official, str):
        problems.append(f"{path}.official_url 必须是 null 或字符串")

    if not isinstance(event.get("is_demo"), bool):
        problems.append(f"{path}.is_demo 必须是布尔值")
    elif event.get("is_demo") != (mode == "demo"):
        problems.append(f"{path}.is_demo 与 payload.mode 不一致（禁止演示/真实数据混用）")

    if event.get("verification_status") == "verified" and not event.get("source_fact"):
        problems.append(f"{path} 标记为 verified 但缺少 source_fact")

    return problems


def assert_valid_payload(payload: Any) -> dict[str, Any]:
    problems = validate_payload(payload)
    if problems:
        raise ValueError("payload 校验失败：" + "；".join(problems))
    return payload


def is_demo_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("mode") == "demo":
        return True
    events = payload.get("events")
    return isinstance(events, list) and any(
        isinstance(e, dict) and e.get("is_demo") for e in events
    )


def normalize_demo_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """演示数据归一化：只做不涉及编造的安全修正（模式标记、统计重算、悬空 highlights 过滤）。

    演示数据由前端/主控提供，这里不生成任何新事件，也不改写事件内容。
    """
    normalized = dict(payload)
    normalized["mode"] = "demo"
    events = [event for event in (normalized.get("events") or []) if isinstance(event, dict)]
    for event in events:
        event["is_demo"] = True
    normalized["events"] = events
    normalized["stats"] = compute_stats(events)
    ids = {event.get("event_id") for event in events}
    normalized["highlights"] = [item for item in (normalized.get("highlights") or []) if item in ids]
    if not isinstance(normalized.get("sources"), list):
        normalized["sources"] = []
    if not isinstance(normalized.get("warnings"), list):
        normalized["warnings"] = []
    if not isinstance(normalized.get("period"), dict):
        normalized["period"] = build_period()
    if "generated_at" not in normalized:
        normalized["generated_at"] = None
    return normalized


def host_of(url: str) -> str:
    return url_host(url)


def payload_fingerprint(payload: dict[str, Any]) -> str:
    ids = ",".join(sorted(str(e.get("event_id")) for e in payload.get("events") or []))
    return hash_id(payload.get("mode", ""), payload.get("period", {}).get("week", ""), ids, length=16)


def generated_at_now(moment: Optional[datetime] = None) -> str:
    return iso_now(moment)
