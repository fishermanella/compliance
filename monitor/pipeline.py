"""采集流水线：采集 → 去重 → 官方来源核验 → AI 分析 → 报表 → 发布。

- 真实运行（live）与演示导出（demo）完全分离：demo 不写入 latest/history。
- 同一时刻只允许一个真实运行（flock 互斥，服务端与 CLI 共用）。
- 任一步失败都会被记录为步骤状态 error，并且不会伪造成功数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import collect_dataguidance, collect_podcast, collect_wechat
from .ai import analyze_events
from .export_xlsx import export_to_path
from .models import CollectionResult
from .official import apply_official, authority_domains
from .schema import (
    SOURCE_NAMES,
    assert_valid_payload,
    build_payload,
    build_period,
    dedupe_events,
    generated_at_now,
    normalize_demo_payload,
)
from .store import Store, history_entry
from .util import RunLock, now_utc

STEPS: tuple[str, ...] = (
    "载入配置",
    "采集 DataGuidance",
    "采集微信公众号",
    "采集播客",
    "去重与统计",
    "官方来源核验",
    "AI 分析",
    "生成 Excel 报表",
    "写入发布数据",
)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_COMPLETE = "complete"
STEP_ERROR = "error"
STEP_SKIPPED = "skipped"

DEMO_ONLY_STEPS = ("载入演示数据", "生成 Excel 报表")


class RunLocked(RuntimeError):
    """已有真实运行在进行中。"""


class DemoUnavailable(RuntimeError):
    """演示数据不可用或不符合契约。"""


@dataclass
class RunOutcome:
    run_id: str
    mode: str
    payload: dict[str, Any]
    report_path: Optional[str] = None
    report_url: Optional[str] = None
    data_url: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_result(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "generated_at": self.payload.get("generated_at"),
            "period": self.payload.get("period"),
            "event_count": len(self.payload.get("events") or []),
            "stats": self.payload.get("stats"),
            "report_url": self.report_url,
            "data_url": self.data_url,
            "warnings": list(self.warnings),
        }


def _noop_step(_label: str, _status: str) -> None:
    return None


class Pipeline:
    def __init__(
        self,
        base_dir: Path | str,
        config: dict[str, Any],
        *,
        store: Optional[Store] = None,
        http: Any = None,
        browser: Any = None,
        now: Optional[datetime] = None,
        ai_transport: Optional[Callable[..., dict[str, Any]]] = None,
        collectors: Optional[dict[str, Callable[..., CollectionResult]]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.config = config
        self.store = store or Store(self.base_dir, config)
        self.http = http
        self.browser = browser
        self.now = now
        self.ai_transport = ai_transport
        self.sleep = sleep
        self.collectors = collectors or {
            "dataguidance": collect_dataguidance.collect,
            "wechat": collect_wechat.collect,
            "podcast": collect_podcast.collect,
        }

    # ------------------------------------------------------------------
    def run(
        self,
        *,
        mode: str = "live",
        run_id: Optional[str] = None,
        use_lock: bool = True,
        on_step: Optional[Callable[[str, str], None]] = None,
    ) -> RunOutcome:
        report_step = on_step or _noop_step
        started = time.monotonic()
        moment = self.now or now_utc()
        run_id = run_id or Store.make_run_id(moment, suffix=mode if mode == "demo" else "")

        if mode == "demo":
            outcome = self._run_demo(run_id=run_id, moment=moment, report_step=report_step)
            outcome.duration_seconds = time.monotonic() - started
            return outcome

        lock = RunLock(self.store.lock_path)
        acquired = lock.acquire(owner={"run_id": run_id, "mode": mode}) if use_lock else False
        if use_lock and not acquired:
            raise RunLocked("已有真实运行在进行中（并发运行被拒绝）")
        try:
            outcome = self._run_live(run_id=run_id, moment=moment, report_step=report_step)
        finally:
            if use_lock:
                lock.release()
        outcome.duration_seconds = time.monotonic() - started
        return outcome

    # ------------------------------------------------------------------
    def _run_live(
        self,
        *,
        run_id: str,
        moment: datetime,
        report_step: Callable[[str, str], None],
    ) -> RunOutcome:
        warnings: list[str] = []

        report_step("载入配置", STEP_RUNNING)
        _, history_warnings = self.store.ensure_history_complete()
        warnings.extend(history_warnings)
        report_step("载入配置", STEP_COMPLETE)

        results: dict[str, CollectionResult] = {}
        for label, source_id in (
            ("采集 DataGuidance", "dataguidance"),
            ("采集微信公众号", "wechat"),
            ("采集播客", "podcast"),
        ):
            report_step(label, STEP_RUNNING)
            try:
                results[source_id] = self._collect(source_id, moment=moment)
            except Exception as exc:
                from .models import SourceRecord

                record = SourceRecord(
                    id=source_id,
                    name=SOURCE_NAMES[source_id],
                    status="error",
                    coverage_status="failed",
                    error=f"采集器异常：{exc}",
                )
                results[source_id] = CollectionResult(source=record)
                warnings.append(f"{SOURCE_NAMES[source_id]} 采集器异常：{exc}")
            record = results[source_id].source
            for message in record.warnings:
                warnings.append(f"[{record.name}] {message}")
            if record.error:
                warnings.append(f"[{record.name}] {record.error}")
            if record.status in ("ok", "partial"):
                step_status = STEP_COMPLETE
            elif record.status == "not_configured":
                step_status = STEP_SKIPPED
            else:
                step_status = STEP_ERROR
            report_step(label, step_status)

        # 私有原始正文
        for source_id, result in results.items():
            if result.raw_pages:
                suffix = ".xml" if source_id == "podcast" else ".html"
                self.store.save_raw(run_id, source_id, result.raw_pages, suffix=suffix)

        report_step("去重与统计", STEP_RUNNING)
        all_events: list[dict[str, Any]] = []
        for source_id in ("dataguidance", "wechat", "podcast"):
            all_events.extend(results[source_id].events)
        deduped, dedupe_warnings = dedupe_events(all_events, authority_domains=authority_domains(self.config))
        warnings.extend(dedupe_warnings)
        report_step("去重与统计", STEP_COMPLETE)

        report_step("官方来源核验", STEP_RUNNING)
        raw_pages: dict[str, str] = {}
        for result in results.values():
            raw_pages.update(result.raw_pages)
        try:
            deduped, official_warnings = apply_official(
                self.config, deduped, raw_pages=raw_pages, http=self.http
            )
            warnings.extend(official_warnings)
            report_step("官方来源核验", STEP_COMPLETE)
        except Exception as exc:
            warnings.append(f"官方来源核验失败（已跳过，不影响其他步骤）：{exc}")
            report_step("官方来源核验", STEP_ERROR)

        report_step("AI 分析", STEP_RUNNING)
        try:
            deduped, ai_warnings = analyze_events(self.config, deduped, transport=self.ai_transport)
            warnings.extend(ai_warnings)
            analyzed = sum(1 for e in deduped if e.get("analysis_status") == "ok")
            report_step("AI 分析", STEP_COMPLETE if analyzed else STEP_SKIPPED)
        except Exception as exc:
            warnings.append(f"AI 分析失败（全部条目保持 needs_review）：{exc}")
            for event in deduped:
                event["relevance"] = "needs_review"
                event["status"] = "Needs Review"
                event["analysis_status"] = "error"
            report_step("AI 分析", STEP_ERROR)

        sources_public = [results[source_id].source.to_public() for source_id in ("dataguidance", "wechat", "podcast")]
        payload = build_payload(
            mode="live",
            sources=sources_public,
            events=deduped,
            highlights=_pick_highlights(deduped),
            warnings=_dedupe_warnings(warnings),
            generated_at=generated_at_now(moment),
            period=build_period(moment),
        )
        assert_valid_payload(payload)

        report_step("生成 Excel 报表", STEP_RUNNING)
        report_path = export_to_path(payload, self.store.reports_dir / f"{run_id}.xlsx")
        report_step("生成 Excel 报表", STEP_COMPLETE)

        report_step("写入发布数据", STEP_RUNNING)
        self.store.write_week(run_id, payload)
        self.store.write_latest(payload)
        self.store.append_history(
            history_entry(
                run_id,
                payload,
                report_url=Store.report_url(run_id),
                data_url=Store.data_url(run_id),
            )
        )
        report_step("写入发布数据", STEP_COMPLETE)

        return RunOutcome(
            run_id=run_id,
            mode="live",
            payload=payload,
            report_path=str(report_path),
            report_url=Store.report_url(run_id),
            data_url=Store.data_url(run_id),
            warnings=list(payload["warnings"]),
        )

    # ------------------------------------------------------------------
    def _run_demo(
        self,
        *,
        run_id: str,
        moment: datetime,
        report_step: Callable[[str, str], None],
    ) -> RunOutcome:
        for label in STEPS:
            if label in DEMO_ONLY_STEPS:
                continue
            report_step(label, STEP_SKIPPED)

        report_step("载入演示数据", STEP_RUNNING)
        demo = self.store.read_demo()
        if not isinstance(demo, dict):
            raise DemoUnavailable(
                "未找到演示数据 public/data/demo.json（该文件由主控/前端提供，后端不会自行生成）。"
            )
        demo = normalize_demo_payload(demo)
        problems = _validate(demo)
        if problems:
            raise DemoUnavailable("演示数据不符合 payload 契约：" + "；".join(problems[:6]))
        report_step("载入演示数据", STEP_COMPLETE)

        report_step("生成 Excel 报表", STEP_RUNNING)
        report_path = export_to_path(demo, self.store.reports_dir / f"{run_id}.xlsx")
        report_step("生成 Excel 报表", STEP_COMPLETE)

        warnings = ["演示数据导出：未写入 latest.json / history.json，不会与真实数据混用。"]
        return RunOutcome(
            run_id=run_id,
            mode="demo",
            payload=demo,
            report_path=str(report_path),
            report_url=Store.report_url(run_id),
            data_url=None,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    def _collect(self, source_id: str, *, moment: datetime) -> CollectionResult:
        collector = self.collectors[source_id]
        return collector(
            self.config,
            base_dir=self.base_dir,
            http=self.http,
            browser=self.browser,
            raw_dir=self.store.raw_dir,
            now=moment,
            sleep=self.sleep,
        )


def _validate(payload: dict[str, Any]) -> list[str]:
    from .schema import validate_payload

    return validate_payload(payload)


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for message in warnings:
        text = str(message).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _pick_highlights(events: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    high = [e["event_id"] for e in events if e.get("relevance") == "high"]
    if len(high) >= limit:
        return high[:limit]
    medium = [e["event_id"] for e in events if e.get("relevance") == "medium"]
    return (high + medium)[:limit]


def step_labels() -> list[str]:
    return list(STEPS)
