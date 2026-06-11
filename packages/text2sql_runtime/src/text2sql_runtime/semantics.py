from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_yaml


@dataclass(frozen=True)
class SemanticConcept:
    name: str
    display_name: str
    keywords: list[str]
    object_name: str | None
    rule: dict[str, Any]


class SemanticIndex:
    def __init__(self, concepts: dict[str, SemanticConcept], sensitive_keywords: list[str]) -> None:
        self.concepts = concepts
        self.sensitive_keywords = sensitive_keywords

    @classmethod
    def from_config(cls, path: Path) -> "SemanticIndex":
        raw = load_yaml(path)
        concepts = {
            name: SemanticConcept(
                name=name,
                display_name=str(item.get("display_name", name)),
                keywords=[str(keyword) for keyword in item.get("keywords", [])],
                object_name=item.get("object"),
                rule=dict(item.get("rule", {})),
            )
            for name, item in raw.get("concepts", {}).items()
        }
        return cls(
            concepts=concepts,
            sensitive_keywords=[str(item) for item in raw.get("sensitive_search_keywords", [])],
        )

    def detect(self, question: str) -> list[SemanticConcept]:
        return [
            concept
            for concept in self.concepts.values()
            if any(keyword.lower() in question.lower() for keyword in concept.keywords)
        ]

    def contains_sensitive_search(self, question: str) -> bool:
        lowered = question.lower()
        return any(keyword.lower() in lowered for keyword in self.sensitive_keywords)


def epoch_ms_for_age_at_least(age: int, today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    try:
        cutoff = current.replace(year=current.year - age)
    except ValueError:
        cutoff = current.replace(month=2, day=28, year=current.year - age)
    return int(dt.datetime.combine(cutoff, dt.time.min).timestamp() * 1000)


def month_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = dt.date(current.year, current.month, 1)
    return int(dt.datetime.combine(start, dt.time.min).timestamp() * 1000)


def month_end_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    if current.month == 12:
        next_month = dt.date(current.year + 1, 1, 1)
    else:
        next_month = dt.date(current.year, current.month + 1, 1)
    return int(dt.datetime.combine(next_month, dt.time.min).timestamp() * 1000)


def week_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = current - dt.timedelta(days=current.weekday())
    return int(dt.datetime.combine(start, dt.time.min).timestamp() * 1000)


def week_end_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = current - dt.timedelta(days=current.weekday())
    end = start + dt.timedelta(days=7)
    return int(dt.datetime.combine(end, dt.time.min).timestamp() * 1000)
