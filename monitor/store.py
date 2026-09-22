"""发布产物读写：public/data/latest.json、public/data/weeks/<id>.json、public/data/history.json、public/reports/*.xlsx。

隔离与安全：
- 真实数据（live）与演示数据（demo）严格分离：demo 永不写入 latest.json / history.json。
- 原始正文只写 data/raw（私有），绝不进入 public/。
- 写入使用原子替换；历史索引损坏时先备份再重建，绝不静默清空。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .config import path_of
from .schema import assert_valid_payload, is_demo_payload
from .util import (
    UnsafePath,
    atomic_write_json,
    atomic_write_text,
    hash_id,
    is_safe_name,
    iso_now,
    now_utc,
    read_json_or_none,
    resolve_within,
)


class DemoDataRejected(ValueError):
    """拒绝把演示数据写入真实数据位置。"""


class Store:
    def __init__(self, base_dir: Path | str, config: dict[str, Any]) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.config = config
        self.public_dir = path_of(config, "public_dir", self.base_dir)
        self.data_dir = path_of(config, "data_dir", self.base_dir)
        self.raw_dir = path_of(config, "raw_dir", self.base_dir)
        self.wechat_links = path_of(config, "wechat_links_file", self.base_dir)
        self.demo_path = path_of(config, "demo_payload", self.base_dir)
        self.latest_path = path_of(config, "latest_payload", self.base_dir)
        self.history_path = path_of(config, "history_index", self.base_dir)
        self.weeks_dir = path_of(config, "weeks_dir", self.base_dir)
        self.reports_dir = path_of(config, "reports_dir", self.base_dir)
        self.lock_path = path_of(config, "lock_file", self.base_dir)

    # ---------------- 读取 ----------------

    def read_latest(self) -> Optional[dict[str, Any]]:
        payload = read_json_or_none(self.latest_path)
        return payload if isinstance(payload, dict) else None

    def read_demo(self) -> Optional[dict[str, Any]]:
        payload = read_json_or_none(self.demo_path)
        return payload if isinstance(payload, dict) else None

    def read_history(self) -> list[dict[str, Any]]:
        data = read_json_or_none(self.history_path)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def read_week(self, run_id: str) -> Optional[dict[str, Any]]:
        if not is_safe_name(run_id):
            return None
        payload = read_json_or_none(self.weeks_dir / f"{run_id}.json")
        return payload if isinstance(payload, dict) else None

    def report_path(self, run_id: str) -> Optional[Path]:
        if not is_safe_name(run_id):
            return None
        return self.reports_dir / f"{run_id}.xlsx"

    @staticmethod
    def report_url(run_id: str) -> str:
        return f"/reports/{run_id}.xlsx"

    @staticmethod
    def data_url(run_id: str) -> str:
        return f"/data/weeks/{run_id}.json"

    # ---------------- 运行标识 ----------------

    @staticmethod
    def make_run_id(moment: Any = None, *, suffix: str = "") -> str:
        stamp = (moment or now_utc()).strftime("%Y%m%dT%H%M%SZ")
        base = f"run-{stamp}"
        if suffix:
            safe_suffix = "".join(ch for ch in str(suffix) if ch.isalnum() or ch in "-_")[:16]
            if safe_suffix:
                base = f"{base}-{safe_suffix}"
        return base

    # ---------------- 写入 ----------------

    def write_latest(self, payload: dict[str, Any]) -> None:
        if is_demo_payload(payload):
            raise DemoDataRejected("拒绝把演示数据写入 latest.json（真实/演示数据必须隔离）")
        assert_valid_payload(payload)
        atomic_write_json(self.latest_path, payload)

    def write_week(self, run_id: str, payload: dict[str, Any]) -> Path:
        if not is_safe_name(run_id):
            raise ValueError(f"非法 run_id：{run_id}")
        if is_demo_payload(payload):
            raise DemoDataRejected("拒绝把演示数据写入 weeks/ 目录")
        assert_valid_payload(payload)
        path = self.weeks_dir / f"{run_id}.json"
        atomic_write_json(path, payload)
        return path

    def append_history(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """追加历史索引条目；同 id 覆盖。历史损坏时先备份再重建，绝不静默丢失。"""
        run_id = str(entry.get("id") or "")
        if not is_safe_name(run_id):
            raise ValueError(f"非法历史条目 id：{run_id}")
        if entry.get("mode") == "demo":
            raise DemoDataRejected("演示数据不写入 history.json")

        history, _history_warnings = self._load_history_resilient()
        history = [item for item in history if str(item.get("id")) != run_id]
        history.append(entry)
        atomic_write_json(self.history_path, history)
        return history

    def _load_history_resilient(self) -> tuple[list[dict[str, Any]], list[str]]:
        """读取历史索引；损坏时先备份再按 weeks/ 重建，并显式告警（绝不静默清空）。"""
        if not self.history_path.exists():
            return [], []
        data = read_json_or_none(self.history_path)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)], []
        backup = self.history_path.with_suffix(f".corrupt-{now_utc().strftime('%Y%m%dT%H%M%SZ')}.json")
        try:
            self.history_path.replace(backup)
            backup_note = f"（已备份为 {backup.name}）"
        except OSError:
            backup_note = "（备份失败，原文件保留）"
        rebuilt = self.rebuild_history_from_weeks()
        return rebuilt, [
            f"历史索引 history.json 无法解析，已按 public/data/weeks/ 重建 {len(rebuilt)} 条记录{backup_note}。"
        ]

    def rebuild_history_from_weeks(self) -> list[dict[str, Any]]:
        """从 public/data/weeks/*.json 重建历史索引（历史保留的兜底手段）。"""
        entries: list[dict[str, Any]] = []
        if not self.weeks_dir.is_dir():
            return entries
        for path in sorted(self.weeks_dir.glob("*.json")):
            run_id = path.stem
            if not is_safe_name(run_id):
                continue
            payload = read_json_or_none(path)
            if not isinstance(payload, dict) or payload.get("mode") == "demo":
                continue
            entries.append(
                {
                    "id": run_id,
                    "period": (payload.get("period") or {}).get("week", ""),
                    "generated_at": payload.get("generated_at"),
                    "event_count": len(payload.get("events") or []),
                    "report_url": self.report_url(run_id),
                    "data_url": self.data_url(run_id),
                    "mode": "live",
                }
            )
        return entries

    def ensure_history_complete(self) -> tuple[list[dict[str, Any]], list[str]]:
        """补齐 history 中缺失的 weeks 条目（不删除任何已有条目）。"""
        history, warnings = self._load_history_resilient()
        known = {str(item.get("id")) for item in history}
        rebuilt = self.rebuild_history_from_weeks()
        missing = [item for item in rebuilt if item["id"] not in known]
        if missing:
            history = history + missing
            atomic_write_json(self.history_path, history)
            warnings.append(f"历史索引已从 weeks/ 补齐 {len(missing)} 条缺失记录。")
        elif not warnings and history:
            atomic_write_json(self.history_path, history)
        return history, warnings

    def save_raw(
        self,
        run_id: str,
        source_id: str,
        pages: dict[str, str],
        *,
        suffix: str = ".html",
    ) -> Optional[Path]:
        """把原始正文写入私有目录 data/raw/<run_id>/<source>/（该目录被 .gitignore 忽略）。"""
        if not is_safe_name(run_id) or not pages:
            return None
        if not is_safe_name(source_id):
            return None
        target_dir = self.raw_dir / run_id / source_id
        target_dir.mkdir(parents=True, exist_ok=True)
        index: dict[str, str] = {}
        for position, (url, text) in enumerate(pages.items(), start=1):
            ext = ".xml" if url.endswith((".xml", ".rss")) else suffix
            name = f"{position:03d}-{hash_id(url, length=10)}{ext}"
            atomic_write_text(target_dir / name, text or "")
            index[name] = url
        atomic_write_json(target_dir / "_index.json", {"captured_at": iso_now(), "pages": index})
        return target_dir

    def public_file(self, relative: str) -> Optional[Path]:
        try:
            return resolve_within(self.public_dir, relative)
        except UnsafePath:
            return None


def history_entry(run_id: str, payload: dict[str, Any], *, report_url: str, data_url: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "period": (payload.get("period") or {}).get("week", ""),
        "generated_at": payload.get("generated_at"),
        "event_count": len(payload.get("events") or []),
        "report_url": report_url,
        "data_url": data_url,
        "mode": payload.get("mode", "live"),
    }
