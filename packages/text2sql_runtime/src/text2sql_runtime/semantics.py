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


_EPOCH = dt.datetime(1970, 1, 1)


def _date_to_epoch_ms(date_val: dt.date) -> int:
    """Convert a date to epoch milliseconds.

    Uses manual arithmetic instead of .timestamp() to support pre-1970
    dates on Windows where the C runtime rejects negative timestamps.
    """
    return int((dt.datetime.combine(date_val, dt.time.min) - _EPOCH).total_seconds() * 1000)

def is_set_epoch_ms_sql(column: str) -> str:
    return f"({column} IS NOT NULL AND {column} <> 0)"


def epoch_ms_for_age_at_least(age: int, today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    try:
        cutoff = current.replace(year=current.year - age)
    except ValueError:
        cutoff = current.replace(month=2, day=28, year=current.year - age)
    return _date_to_epoch_ms(cutoff)


def month_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = dt.date(current.year, current.month, 1)
    return _date_to_epoch_ms(start)


def year_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = dt.date(current.year, 1, 1)
    return int(dt.datetime.combine(start, dt.time.min).timestamp() * 1000)


def month_end_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    if current.month == 12:
        next_month = dt.date(current.year + 1, 1, 1)
    else:
        next_month = dt.date(current.year, current.month + 1, 1)
    return _date_to_epoch_ms(next_month)


def week_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = current - dt.timedelta(days=current.weekday())
    return _date_to_epoch_ms(start)


def week_end_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = current - dt.timedelta(days=current.weekday())
    end = start + dt.timedelta(days=7)
    return _date_to_epoch_ms(end)
