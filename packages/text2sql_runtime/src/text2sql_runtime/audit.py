from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .rejection_reasons import UNCONFIGURED_SEMANTIC_REASON


class SQLiteAuditStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def record(self, entry: dict[str, Any]) -> None:
        payload = dict(entry)
        payload.setdefault("created_at", int(time.time() * 1000))
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO query_audit (
                  query_id, created_at, user_id, domain_id, question, status,
                  hit_path, sql, rejection_reason, elapsed_ms, scanned_rows,
                  explain_json, result_json, warnings_json, interaction_logs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["query_id"],
                    payload["created_at"],
                    payload.get("user_id"),
                    payload.get("domain_id"),
                    payload.get("question"),
                    payload.get("status"),
                    payload.get("hit_path"),
                    payload.get("sql"),
                    payload.get("rejection_reason"),
                    payload.get("elapsed_ms", 0),
                    payload.get("scanned_rows", 0),
                    json.dumps(payload.get("explain", []), ensure_ascii=False),
                    json.dumps(payload.get("result", {}), ensure_ascii=False, default=str),
                    json.dumps(payload.get("warnings", []), ensure_ascii=False),
                    json.dumps(payload.get("interaction_logs", []), ensure_ascii=False, default=str),
                ),
            )

    def get(self, query_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM query_audit WHERE query_id = ?",
                (query_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["explain"] = json.loads(data.pop("explain_json") or "[]")
        data["result"] = json.loads(data.pop("result_json") or "{}")
        data["warnings"] = json.loads(data.pop("warnings_json") or "[]")
        data["interaction_logs"] = json.loads(data.pop("interaction_logs_json", None) or "[]")
        return data

    def unsupported_questions(
        self,
        *,
        limit: int = 50,
        since_ms: int | None = None,
        reason: str = UNCONFIGURED_SEMANTIC_REASON,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        filters = [
            "qa.status = 'rejected'",
            "qa.rejection_reason = ?",
        ]
        params: list[Any] = [reason]
        if since_ms is not None:
            filters.append("qa.created_at >= ?")
            params.append(int(since_ms))
        where_clause = " AND ".join(filters)
        latest_where_clause = where_clause.replace("qa.", "latest.")
        latest_params = list(params)
        query = f"""
            SELECT
              qa.question,
              COUNT(*) AS count,
              MIN(qa.created_at) AS first_seen_at,
              MAX(qa.created_at) AS latest_seen_at,
              (
                SELECT latest.query_id
                FROM query_audit latest
                WHERE latest.question = qa.question AND {latest_where_clause}
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_query_id,
              (
                SELECT latest.domain_id
                FROM query_audit latest
                WHERE latest.question = qa.question AND {latest_where_clause}
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_domain_id,
              (
                SELECT latest.user_id
                FROM query_audit latest
                WHERE latest.question = qa.question AND {latest_where_clause}
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_user_id
            FROM query_audit qa
            WHERE {where_clause}
            GROUP BY qa.question
            ORDER BY latest_seen_at DESC
            LIMIT ?
        """
        query_params = [
            *latest_params,
            *latest_params,
            *latest_params,
            *params,
            bounded_limit,
        ]
        count_query = f"""
            SELECT COUNT(*) AS total, COUNT(DISTINCT qa.question) AS unique_count
            FROM query_audit qa
            WHERE {where_clause}
        """
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, query_params).fetchall()
            summary = connection.execute(count_query, params).fetchone()
        return {
            "reason": reason,
            "total": int(summary["total"] if summary else 0),
            "unique": int(summary["unique_count"] if summary else 0),
            "limit": bounded_limit,
            "sinceMs": since_ms,
            "items": [
                {
                    "question": row["question"],
                    "count": int(row["count"]),
                    "firstSeenAt": int(row["first_seen_at"]),
                    "latestSeenAt": int(row["latest_seen_at"]),
                    "latestQueryId": row["latest_query_id"],
                    "domainId": row["latest_domain_id"],
                    "userId": row["latest_user_id"],
                }
                for row in rows
            ],
        }

    def new_unsupported_questions(
        self,
        *,
        since_ms: int,
        until_ms: int | None = None,
        limit: int = 50,
        reason: str = UNCONFIGURED_SEMANTIC_REASON,
        exclude_user_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        filters = [
            "qa.status = 'rejected'",
            "qa.rejection_reason = ?",
        ]
        params: list[Any] = [reason]
        if exclude_user_ids:
            placeholders = ", ".join("?" for _ in exclude_user_ids)
            filters.append(f"(qa.user_id IS NULL OR qa.user_id NOT IN ({placeholders}))")
            params.extend(exclude_user_ids)
        where_clause = " AND ".join(filters)
        having_parts = ["MIN(qa.created_at) >= ?"]
        having_params: list[Any] = [int(since_ms)]
        if until_ms is not None:
            having_parts.append("MIN(qa.created_at) < ?")
            having_params.append(int(until_ms))
        having_clause = " AND ".join(having_parts)
        query = f"""
            SELECT
              qa.question,
              COUNT(*) AS count,
              MIN(qa.created_at) AS first_seen_at,
              MAX(qa.created_at) AS latest_seen_at,
              (
                SELECT latest.query_id
                FROM query_audit latest
                WHERE latest.question = qa.question
                  AND latest.status = 'rejected'
                  AND latest.rejection_reason = ?
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_query_id,
              (
                SELECT latest.domain_id
                FROM query_audit latest
                WHERE latest.question = qa.question
                  AND latest.status = 'rejected'
                  AND latest.rejection_reason = ?
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_domain_id,
              (
                SELECT latest.user_id
                FROM query_audit latest
                WHERE latest.question = qa.question
                  AND latest.status = 'rejected'
                  AND latest.rejection_reason = ?
                ORDER BY latest.created_at DESC
                LIMIT 1
              ) AS latest_user_id
            FROM query_audit qa
            WHERE {where_clause}
            GROUP BY qa.question
            HAVING {having_clause}
            ORDER BY first_seen_at DESC
            LIMIT ?
        """
        query_params = [
            reason,
            reason,
            reason,
            *params,
            *having_params,
            bounded_limit,
        ]
        count_query = f"""
            SELECT COUNT(*) AS unique_count
            FROM (
              SELECT qa.question
              FROM query_audit qa
              WHERE {where_clause}
              GROUP BY qa.question
              HAVING {having_clause}
            )
        """
        count_params = [*params, *having_params]
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, query_params).fetchall()
            summary = connection.execute(count_query, count_params).fetchone()
        return {
            "reason": reason,
            "unique": int(summary["unique_count"] if summary else 0),
            "limit": bounded_limit,
            "sinceMs": since_ms,
            "untilMs": until_ms,
            "items": [
                {
                    "question": row["question"],
                    "count": int(row["count"]),
                    "firstSeenAt": int(row["first_seen_at"]),
                    "latestSeenAt": int(row["latest_seen_at"]),
                    "latestQueryId": row["latest_query_id"],
                    "domainId": row["latest_domain_id"],
                    "userId": row["latest_user_id"],
                }
                for row in rows
            ],
        }

    def top_questions(
        self,
        *,
        since_ms: int,
        until_ms: int | None = None,
        limit: int = 10,
        exclude_user_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        filters: list[str] = ["qa.created_at >= ?"]
        params: list[Any] = [int(since_ms)]
        if until_ms is not None:
            filters.append("qa.created_at < ?")
            params.append(int(until_ms))
        if exclude_user_ids:
            placeholders = ", ".join("?" for _ in exclude_user_ids)
            filters.append(f"(qa.user_id IS NULL OR qa.user_id NOT IN ({placeholders}))")
            params.extend(exclude_user_ids)
        where_clause = " AND ".join(filters)
        query = f"""
            SELECT
              qa.question,
              COUNT(*) AS count,
              MIN(qa.created_at) AS first_seen_at,
              MAX(qa.created_at) AS latest_seen_at,
              SUM(CASE WHEN qa.status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
              SUM(
                CASE
                  WHEN qa.rejection_reason = ? THEN 1
                  ELSE 0
                END
              ) AS unsupported_count
            FROM query_audit qa
            WHERE {where_clause}
            GROUP BY qa.question
            ORDER BY count DESC, latest_seen_at DESC
            LIMIT ?
        """
        query_params = [UNCONFIGURED_SEMANTIC_REASON, *params, bounded_limit]
        summary_query = f"""
            SELECT COUNT(*) AS total, COUNT(DISTINCT qa.question) AS unique_count
            FROM query_audit qa
            WHERE {where_clause}
        """
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, query_params).fetchall()
            summary = connection.execute(summary_query, params).fetchone()
        return {
            "total": int(summary["total"] if summary else 0),
            "unique": int(summary["unique_count"] if summary else 0),
            "limit": bounded_limit,
            "sinceMs": since_ms,
            "untilMs": until_ms,
            "items": [
                {
                    "question": row["question"],
                    "count": int(row["count"]),
                    "firstSeenAt": int(row["first_seen_at"]),
                    "latestSeenAt": int(row["latest_seen_at"]),
                    "rejectedCount": int(row["rejected_count"]),
                    "unsupportedCount": int(row["unsupported_count"]),
                }
                for row in rows
            ],
        }

    def _init(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS query_audit (
                  query_id TEXT PRIMARY KEY,
                  created_at INTEGER NOT NULL,
                  user_id TEXT,
                  domain_id TEXT,
                  question TEXT NOT NULL,
                  status TEXT NOT NULL,
                  hit_path TEXT,
                  sql TEXT,
                  rejection_reason TEXT,
                  elapsed_ms INTEGER NOT NULL,
                  scanned_rows INTEGER NOT NULL,
                  explain_json TEXT NOT NULL,
                  result_json TEXT NOT NULL,
                  warnings_json TEXT NOT NULL,
                  interaction_logs_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(query_audit)").fetchall()
            }
            if "interaction_logs_json" not in columns:
                connection.execute(
                    "ALTER TABLE query_audit ADD COLUMN interaction_logs_json TEXT NOT NULL DEFAULT '[]'"
                )
