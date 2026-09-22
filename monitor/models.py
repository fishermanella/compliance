"""采集结果的数据结构（内部使用，不直接对外发布）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SourceRecord:
    """对应 payload.sources[] 中的一条来源记录。"""

    id: str
    name: str
    status: str = "idle"
    coverage_status: str = "unknown"
    collected_count: int = 0
    expected_count: Optional[int] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "coverage_status": self.coverage_status,
            "collected_count": int(self.collected_count),
            "expected_count": None if self.expected_count is None else int(self.expected_count),
            "error": self.error,
        }


@dataclass
class CollectionResult:
    """单个来源的采集产物。

    events: 已构造为 payload 事件结构的 dict 列表（AI 分析前）。
    raw_pages: url -> 原始 HTML/文本，仅用于内部解析与官方链接抽取，写入 data/raw（私有）。
    """

    source: SourceRecord
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_pages: dict[str, str] = field(default_factory=dict)
    expected_count: Optional[int] = None

    def __post_init__(self) -> None:
        if self.expected_count is not None and self.source.expected_count is None:
            self.source.expected_count = self.expected_count
        if self.source.collected_count == 0 and self.events:
            self.source.collected_count = len(self.events)
