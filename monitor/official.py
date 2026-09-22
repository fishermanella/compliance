"""官方来源抽取（仅从来源页面的出站链接中寻找权威域名）。

关键原则（与 PRD 一致）：
- 只在配置的权威域名白名单内认定「官方来源」。
- 抓到官方链接本身 ≠ 事实核验完成：默认仍是 official_pending，
  仅在成功取回官方页面内容后标记 official_retrieved，
  只有人工确认（config.local.json 的 human_verified）才会标记 verified。
- 绝不自动升级为 verified。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from .fetch import FetchError, HttpClient, parse_with_bs4
from .util import clean_text, is_authority_url, normalize_url, strip_html

MAX_HTML_CHARS = 2_000_000

_ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)


def authority_domains(config: dict[str, Any]) -> tuple[str, ...]:
    domains = (config.get("official", {}) or {}).get("domains") or ()
    return tuple(str(d).strip().lower() for d in domains if str(d).strip())


def human_verified_map(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("human_verified") or {}
    return value if isinstance(value, dict) else {}


def extract_outbound_links(html: str, base_url: str, *, limit: int = 400) -> list[dict[str, str]]:
    """返回 [{'url':..., 'text':...}]，保持页面出现顺序。

    bs4 缺失时退化为正则解析；两条路径都只读取页面中真实存在的 href，不做任何猜测。
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def _push(href: str, text: str) -> bool:
        href = (href or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return True
        absolute = normalize_url(urljoin(base_url, href))
        if not absolute.startswith("http") or absolute in seen:
            return True
        seen.add(absolute)
        out.append({"url": absolute, "text": text})
        return len(out) < limit

    soup = parse_with_bs4(html)
    if soup is not None:
        for anchor in soup.find_all("a", href=True):
            if not _push(str(anchor.get("href") or ""), clean_text(anchor.get_text(" ", strip=True))):
                break
        return out

    for match in _ANCHOR_RE.finditer(html or ""):
        if not _push(match.group(1), strip_html(match.group(2))):
            break
    return out


def rank_official_candidates(
    links: Iterable[dict[str, str]],
    domains: Iterable[str],
    *,
    exclude_urls: Iterable[str] = (),
    prefer_keywords: Iterable[str] = (),
    limit: int = 3,
) -> list[str]:
    domains = tuple(domains)
    excluded = {normalize_url(u) for u in exclude_urls if u}
    keywords = [str(k).lower() for k in prefer_keywords if str(k).strip()]
    scored: list[tuple[int, int, str]] = []
    for index, link in enumerate(links):
        url = link.get("url") or ""
        if not url or normalize_url(url) in excluded:
            continue
        if not is_authority_url(url, domains):
            continue
        text = (link.get("text") or "").lower()
        score = 2 if any(keyword in text for keyword in keywords) else 1
        scored.append((-score, index, url))
    scored.sort()
    result: list[str] = []
    for _, _, url in scored:
        if url not in result:
            result.append(url)
        if len(result) >= max(1, limit):
            break
    return result


def resolve_official(
    config: dict[str, Any],
    event: dict[str, Any],
    *,
    html: Optional[str] = None,
    http: Any = None,
    cache: Optional[dict[str, str]] = None,
) -> tuple[dict[str, Any], list[str]]:
    """为单个事件解析官方来源。返回 (更新后的事件, 警告列表)。"""
    warnings: list[str] = []
    official_cfg = config.get("official", {}) or {}
    domains = authority_domains(config)
    source_url = event.get("source_url") or ""

    verified_map = human_verified_map(config)
    if event.get("event_id") in verified_map and event.get("source_fact"):
        updated = dict(event)
        updated["verification_status"] = "verified"
        return updated, warnings

    if not domains:
        warnings.append("未配置权威域名白名单，官方来源抽取已跳过。")
        return dict(event), warnings

    if not source_url:
        return dict(event), warnings

    if html is None and bool(official_cfg.get("fetch_pages", True)):
        client = http if http is not None else HttpClient(
            user_agent=str(official_cfg.get("user_agent") or ""),
            timeout=float(official_cfg.get("request_timeout") or 15),
        )
        try:
            html = client.get(source_url).text[:MAX_HTML_CHARS]
        except FetchError as exc:
            warnings.append(f"{event.get('event_id')}: 无法抓取来源页以抽取官方链接（{exc}）。")
            html = None

    # 来源页本身就是权威域名：抓到内容即可标记 official_retrieved（仍非事实核验）
    if is_authority_url(source_url, domains) and html:
        updated = dict(event)
        updated["official_url"] = normalize_url(source_url)
        updated["verification_status"] = "official_retrieved"
        return updated, warnings

    if not html:
        return dict(event), warnings

    candidates = rank_official_candidates(
        extract_outbound_links(html[:MAX_HTML_CHARS], source_url),
        domains,
        exclude_urls=[source_url, event.get("official_url") or ""],
        prefer_keywords=official_cfg.get("prefer_keywords") or (),
        limit=int(official_cfg.get("max_links_per_event") or 3),
    )

    if not candidates:
        return dict(event), warnings

    target = candidates[0]
    updated = dict(event)
    updated["official_url"] = target

    fetched = False
    if bool(official_cfg.get("fetch_pages", True)):
        if cache is not None and target in cache:
            fetched = bool(cache[target])
        else:
            client = http if http is not None else HttpClient(
                user_agent=str(official_cfg.get("user_agent") or ""),
                timeout=float(official_cfg.get("request_timeout") or 15),
            )
            try:
                text = client.get(target).text
                fetched = bool(text)
            except FetchError as exc:
                warnings.append(f"{event.get('event_id')}: 官方链接取回失败（{exc}），保持 official_pending。")
                fetched = False
            if cache is not None:
                cache[target] = "1" if fetched else ""
    else:
        fetched = False

    # 关键：抓到官方页面也只是 official_retrieved，绝不等于事实已核验
    updated["verification_status"] = "official_retrieved" if fetched else "official_pending"
    if not fetched:
        warnings.append(
            f"{event.get('event_id')}: 已定位官方链接但未取回内容，verification_status=official_pending。"
        )
    return updated, warnings


def apply_official(
    config: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    raw_pages: Optional[dict[str, str]] = None,
    http: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_pages = raw_pages or {}
    warnings: list[str] = []
    cache: dict[str, str] = {}
    output: list[dict[str, Any]] = []
    retrieved = 0
    for event in events:
        html = raw_pages.get(event.get("source_url") or "")
        updated, event_warnings = resolve_official(config, event, html=html, http=http, cache=cache)
        warnings.extend(event_warnings)
        if updated.get("verification_status") == "official_retrieved":
            retrieved += 1
        output.append(updated)

    pending = sum(1 for e in output if e.get("verification_status") == "official_pending")
    verified = sum(1 for e in output if e.get("verification_status") == "verified")
    if output:
        warnings.append(
            f"官方来源核验：official_retrieved {retrieved} 条、official_pending {pending} 条、人工已核验 {verified} 条。"
            "抓到官方链接不等于事实核验完成。"
        )
    return output, warnings
