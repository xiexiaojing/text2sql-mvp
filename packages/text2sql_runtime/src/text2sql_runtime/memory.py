from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MEMORY_SCOPES = frozenset({"global", "domain", "user"})
MEMORY_KINDS = frozenset({"caliber", "mapping", "filter", "correction"})

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|\w{2,}")


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    created_at: int
    updated_at: int
    scope: str
    kind: str
    content: str
    title: str | None = None
    domain_id: str | None = None
    user_id: str | None = None
    keywords: tuple[str, ...] = ()
    source_query_id: str | None = None
    confirmed_by: str | None = None
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "memoryId": self.memory_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "scope": self.scope,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "domainId": self.domain_id,
            "userId": self.user_id,
            "keywords": list(self.keywords),
            "sourceQueryId": self.source_query_id,
            "confirmedBy": self.confirmed_by,
            "active": self.active,
        }


def normalize_memory_scope(scope: str) -> str:
    normalized = str(scope or "").strip().lower()
    if normalized not in MEMORY_SCOPES:
        raise ValueError(f"unsupported memory scope: {scope}")
    return normalized


def normalize_memory_kind(kind: str) -> str:
    normalized = str(kind or "correction").strip().lower()
    if normalized not in MEMORY_KINDS:
        raise ValueError(f"unsupported memory kind: {kind}")
    return normalized


def infer_memory_keywords(content: str, title: str | None = None, extra: list[str] | None = None) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for source in (title, content, *(extra or [])):
        if not source:
            continue
        for match in _TOKEN_RE.findall(str(source)):
            token = match.strip()
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens[:24]


def validate_memory_scope_fields(*, scope: str, domain_id: str | None, user_id: str | None) -> None:
    if scope == "global":
        if domain_id or user_id:
            raise ValueError("global memory must not include domainId or userId")
        return
    if scope == "domain":
        if not domain_id:
            raise ValueError("domain memory requires domainId")
        return
    if scope == "user":
        if not domain_id or not user_id:
            raise ValueError("user memory requires domainId and userId")


def score_memory_relevance(question: str, memory: MemoryRecord) -> float:
    normalized_question = re.sub(r"\s+", "", question.strip().lower())
    if not normalized_question:
        return 0.0
    score = 0.0
    for keyword in memory.keywords:
        token = str(keyword).strip().lower()
        if len(token) >= 2 and token in normalized_question:
            score += 3.0
    if memory.title:
        for token in infer_memory_keywords(memory.title):
            if token.lower() in normalized_question:
                score += 1.5
    content = re.sub(r"\s+", "", memory.content.strip().lower())
    if content and content in normalized_question:
        score += 4.0
    for token in infer_memory_keywords(memory.content):
        if len(token) >= 3 and token.lower() in normalized_question:
            score += 1.0
    if memory.scope == "user":
        score += 0.5
    elif memory.scope == "domain":
        score += 0.3
    else:
        score += 0.1
    return score


def format_memory_context_lines(memories: list[MemoryRecord]) -> list[str]:
    if not memories:
        return []
    lines = [
        "Confirmed business memories (apply when relevant; user-approved constraints):",
    ]
    for memory in memories:
        label_parts = [memory.kind, memory.scope]
        if memory.title:
            label_parts.append(memory.title)
        label = "/".join(label_parts)
        lines.append(f"- [{label}] {memory.content}")
    return lines


class SQLiteMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def create(
        self,
        *,
        content: str,
        scope: str,
        kind: str = "correction",
        title: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        keywords: list[str] | None = None,
        source_query_id: str | None = None,
        confirmed_by: str | None = None,
    ) -> MemoryRecord:
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise ValueError("memory content is required")
        if len(normalized_content) > 2000:
            raise ValueError("memory content exceeds 2000 characters")
        normalized_scope = normalize_memory_scope(scope)
        normalized_kind = normalize_memory_kind(kind)
        validate_memory_scope_fields(
            scope=normalized_scope,
            domain_id=domain_id,
            user_id=user_id,
        )
        keyword_list = [
            str(item).strip()
            for item in (keywords or infer_memory_keywords(normalized_content, title))
            if str(item).strip()
        ]
        now = int(time.time() * 1000)
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            scope=normalized_scope,
            kind=normalized_kind,
            content=normalized_content,
            title=str(title).strip() if title else None,
            domain_id=domain_id,
            user_id=user_id,
            keywords=tuple(keyword_list[:24]),
            source_query_id=source_query_id,
            confirmed_by=confirmed_by,
            active=True,
        )
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO query_memories (
                  memory_id, created_at, updated_at, scope, kind, title, content,
                  domain_id, user_id, keywords_json, source_query_id, confirmed_by, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.memory_id,
                    record.created_at,
                    record.updated_at,
                    record.scope,
                    record.kind,
                    record.title,
                    record.content,
                    record.domain_id,
                    record.user_id,
                    json.dumps(list(record.keywords), ensure_ascii=False),
                    record.source_query_id,
                    record.confirmed_by,
                    1 if record.active else 0,
                ),
            )
        return record

    def deactivate(self, memory_id: str) -> bool:
        now = int(time.time() * 1000)
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                """
                UPDATE query_memories
                SET active = 0, updated_at = ?
                WHERE memory_id = ? AND active = 1
                """,
                (now, memory_id),
            )
            return cursor.rowcount > 0

    def list_memories(
        self,
        *,
        domain_id: str | None = None,
        user_id: str | None = None,
        scope: str | None = None,
        limit: int = 50,
        active_only: bool = True,
    ) -> list[MemoryRecord]:
        bounded_limit = max(1, min(int(limit), 200))
        filters = ["1 = 1"]
        params: list[Any] = []
        if active_only:
            filters.append("active = 1")
        if scope:
            filters.append("scope = ?")
            params.append(normalize_memory_scope(scope))
        if domain_id:
            filters.append("(scope = 'global' OR domain_id = ?)")
            params.append(domain_id)
        if user_id:
            filters.append("(scope = 'global' OR scope = 'domain' OR user_id = ?)")
            params.append(user_id)
        query = f"""
            SELECT *
            FROM query_memories
            WHERE {' AND '.join(filters)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(bounded_limit)
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def retrieve(
        self,
        *,
        question: str,
        domain_id: str | None = None,
        user_id: str | None = None,
        limit: int = 3,
        min_score: float = 1.0,
    ) -> list[MemoryRecord]:
        bounded_limit = max(1, min(int(limit), 10))
        candidates = self._candidate_records(domain_id=domain_id, user_id=user_id)
        scored = [
            (score_memory_relevance(question, record), record)
            for record in candidates
        ]
        scored = [(score, record) for score, record in scored if score >= min_score]
        scored.sort(key=lambda item: (-item[0], -item[1].updated_at, item[1].memory_id))
        return [record for _, record in scored[:bounded_limit]]

    def _candidate_records(
        self,
        *,
        domain_id: str | None,
        user_id: str | None,
    ) -> list[MemoryRecord]:
        filters = ["active = 1", "scope = 'global'"]
        params: list[Any] = []
        if domain_id:
            filters.append("(scope = 'domain' AND domain_id = ?)")
            params.append(domain_id)
        if domain_id and user_id:
            filters.append("(scope = 'user' AND domain_id = ? AND user_id = ?)")
            params.extend([domain_id, user_id])
        query = f"""
            SELECT *
            FROM query_memories
            WHERE {' OR '.join(filters)}
            ORDER BY updated_at DESC
            LIMIT 200
        """
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        keywords_raw = json.loads(row["keywords_json"] or "[]")
        keywords = tuple(str(item).strip() for item in keywords_raw if str(item).strip())
        return MemoryRecord(
            memory_id=row["memory_id"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            scope=row["scope"],
            kind=row["kind"],
            title=row["title"],
            content=row["content"],
            domain_id=row["domain_id"],
            user_id=row["user_id"],
            keywords=keywords,
            source_query_id=row["source_query_id"],
            confirmed_by=row["confirmed_by"],
            active=bool(row["active"]),
        )

    def _init(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_memories (
                  memory_id TEXT PRIMARY KEY,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  scope TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  title TEXT,
                  content TEXT NOT NULL,
                  domain_id TEXT,
                  user_id TEXT,
                  keywords_json TEXT NOT NULL DEFAULT '[]',
                  source_query_id TEXT,
                  confirmed_by TEXT,
                  active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_query_memories_scope_domain "
                "ON query_memories(scope, domain_id, active, updated_at DESC)"
            )
