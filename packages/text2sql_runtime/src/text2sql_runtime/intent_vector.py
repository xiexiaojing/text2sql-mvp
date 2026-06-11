from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import IntentVectorSettings


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        """Return one vector for text."""


@dataclass(frozen=True)
class IntentVectorConfig:
    enabled: bool
    distance_threshold: float
    timeout_ms: int
    top_k: int

    @property
    def negative_margin(self) -> float:
        return max(0.01, min(0.05, self.distance_threshold * 0.25))


@dataclass(frozen=True)
class IntentVectorCandidate:
    intent_id: str
    distance: float
    matched_query: str
    negative_distance: float | None = None
    boundary_distance: float | None = None
    boundary_query: str = ""
    boundary_negative_distance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent_id,
            "distance": round(self.distance, 4),
            "matchedQuery": self.matched_query,
            "negativeDistance": _round_optional(self.negative_distance),
            "boundaryDistance": _round_optional(self.boundary_distance),
            "boundaryQuery": self.boundary_query,
            "boundaryNegativeDistance": _round_optional(self.boundary_negative_distance),
        }


@dataclass(frozen=True)
class _VectorEntry:
    intent_id: str
    query: str
    vector: tuple[float, ...]
    kind: str


@dataclass
class _IntentScore:
    intent_id: str
    positive_distance: float | None = None
    positive_query: str = ""
    negative_distance: float | None = None
    boundary_distance: float | None = None
    boundary_query: str = ""
    boundary_negative_distance: float | None = None


class LocalHashEmbeddingProvider:
    """Small offline vectorizer for deterministic tests and dev bootstraps."""

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions if dimensions > 0 else 512

    def embed(self, text: str) -> list[float]:
        tokens = _tokenize(_normalize_query(text))
        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return vector


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingProvider:
    base_url: str
    api_key: str | None
    model: str
    timeout_ms: int
    dimensions: int = 0

    def embed(self, text: str) -> list[float]:
        if not self.base_url.strip():
            raise RuntimeError("intent embedding base URL is not configured")
        if not self.model.strip():
            raise RuntimeError("intent embedding model is not configured")
        payload: dict[str, Any] = {"model": self.model, "input": text}
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        response = httpx.post(
            _embedding_url(self.base_url),
            headers=_embedding_headers(self.api_key),
            json=payload,
            timeout=max(0.001, self.timeout_ms / 1000),
        )
        response.raise_for_status()
        return _embedding_from_payload(response.json())


@dataclass(frozen=True)
class ProxyEmbeddingProvider:
    url: str
    api_key: str | None = None
    model: str | None = None
    timeout_ms: int = 350
    dimensions: int = 0

    def embed(self, text: str) -> list[float]:
        if not self.url.strip():
            raise RuntimeError("intent embedding proxy URL is not configured")
        payload: dict[str, Any] = {"content": text}
        if self.model:
            payload["model"] = self.model
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        response = httpx.post(
            self.url,
            headers=_embedding_headers(self.api_key),
            json=payload,
            timeout=max(0.001, self.timeout_ms / 1000),
        )
        response.raise_for_status()
        return _embedding_from_payload(response.json())


class IntentVectorIndex:
    def __init__(
        self,
        provider: EmbeddingProvider | None,
        config: IntentVectorConfig,
    ) -> None:
        self.provider = provider
        self.config = config
        self._entries: tuple[_VectorEntry, ...] = ()
        self._fingerprint = ""
        self._semantic_fingerprint = ""
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.provider is not None

    def refresh(self, intents: Sequence[Mapping[str, Any]]) -> bool:
        if not self.enabled:
            return False
        fingerprint = _json_fingerprint([dict(intent) for intent in intents])
        semantic_fingerprint = _json_fingerprint(
            [
                {
                    "id": _string(intent.get("id")),
                    "semantic": intent.get("semantic"),
                    "examples": intent.get("examples"),
                    "display_name": intent.get("display_name"),
                }
                for intent in intents
            ]
        )
        with self._lock:
            if fingerprint and fingerprint == self._fingerprint:
                return True
            if semantic_fingerprint and semantic_fingerprint == self._semantic_fingerprint:
                self._fingerprint = fingerprint
                return True

        started = time.monotonic()
        del started
        try:
            entries = tuple(self._build_entries(intents))
        except Exception:
            self._clear()
            return False
        with self._lock:
            self._entries = entries
            self._fingerprint = fingerprint
            self._semantic_fingerprint = semantic_fingerprint
        return bool(entries)

    def search(self, question: str, *, top_k: int | None = None) -> list[IntentVectorCandidate]:
        if not self.enabled:
            return []
        text = _string(question)
        if not text:
            return []
        with self._lock:
            entries = self._entries
        if not entries or self.provider is None:
            return []
        try:
            query_vector = _vector_tuple(self.provider.embed(text))
        except Exception:
            return []
        if not query_vector:
            return []
        candidates = self._allowed_candidates(self._scores(query_vector, entries))
        limit = max(1, int(top_k or self.config.top_k or 1))
        return sorted(candidates, key=lambda candidate: candidate.distance)[:limit]

    def _build_entries(self, intents: Sequence[Mapping[str, Any]]) -> Iterable[_VectorEntry]:
        assert self.provider is not None
        for intent in intents:
            intent_id = _string(intent.get("id"))
            if not intent_id:
                continue
            semantic = intent.get("semantic") if isinstance(intent.get("semantic"), Mapping) else {}
            for query in _unique_strings(
                [
                    *list(_list(semantic.get("queries"))),
                    *list(_list(intent.get("examples"))),
                    _string(intent.get("display_name")),
                ]
            ):
                vector = _vector_tuple(self.provider.embed(query))
                if vector:
                    yield _VectorEntry(intent_id, query, vector, "positive")
            for query in _unique_strings(_list(semantic.get("negative_queries"))):
                vector = _vector_tuple(self.provider.embed(query))
                if vector:
                    yield _VectorEntry(intent_id, query, vector, "negative")
            for query in _unique_strings(_list(semantic.get("boundary_queries"))):
                vector = _vector_tuple(self.provider.embed(query))
                if vector:
                    yield _VectorEntry(intent_id, query, vector, "boundary")
            for query in _unique_strings(_list(semantic.get("boundary_negative_queries"))):
                vector = _vector_tuple(self.provider.embed(query))
                if vector:
                    yield _VectorEntry(intent_id, query, vector, "boundary_negative")

    def _scores(
        self,
        query_vector: tuple[float, ...],
        entries: Sequence[_VectorEntry],
    ) -> dict[str, _IntentScore]:
        scores: dict[str, _IntentScore] = {}
        for entry in entries:
            distance = _cosine_distance(query_vector, entry.vector)
            if distance is None:
                continue
            score = scores.setdefault(entry.intent_id, _IntentScore(entry.intent_id))
            if entry.kind == "negative":
                if score.negative_distance is None or distance < score.negative_distance:
                    score.negative_distance = distance
                continue
            if entry.kind == "boundary":
                if score.boundary_distance is None or distance < score.boundary_distance:
                    score.boundary_distance = distance
                    score.boundary_query = entry.query
                continue
            if entry.kind == "boundary_negative":
                if (
                    score.boundary_negative_distance is None
                    or distance < score.boundary_negative_distance
                ):
                    score.boundary_negative_distance = distance
                continue
            if score.positive_distance is None or distance < score.positive_distance:
                score.positive_distance = distance
                score.positive_query = entry.query
        return scores

    def _allowed_candidates(
        self,
        scores: Mapping[str, _IntentScore],
    ) -> list[IntentVectorCandidate]:
        candidates: list[IntentVectorCandidate] = []
        for score in scores.values():
            positive_distance = score.positive_distance
            if positive_distance is None:
                continue
            if positive_distance > self.config.distance_threshold:
                continue
            negative_distance = score.negative_distance
            if negative_distance is not None:
                if negative_distance <= positive_distance + self.config.negative_margin:
                    continue
            if not self._passes_boundary_filter(score, positive_distance):
                continue
            candidates.append(
                IntentVectorCandidate(
                    intent_id=score.intent_id,
                    distance=positive_distance,
                    matched_query=score.positive_query,
                    negative_distance=negative_distance,
                    boundary_distance=score.boundary_distance,
                    boundary_query=score.boundary_query,
                    boundary_negative_distance=score.boundary_negative_distance,
                )
            )
        return candidates

    def _passes_boundary_filter(self, score: _IntentScore, positive_distance: float) -> bool:
        boundary_distance = score.boundary_distance
        boundary_negative_distance = score.boundary_negative_distance
        if boundary_distance is None:
            if boundary_negative_distance is None:
                return True
            return boundary_negative_distance > positive_distance + self.config.negative_margin
        if boundary_distance > self.config.distance_threshold:
            return False
        if boundary_negative_distance is None:
            return True
        return boundary_negative_distance > boundary_distance + self.config.negative_margin

    def _clear(self) -> None:
        with self._lock:
            self._entries = ()
            self._fingerprint = ""
            self._semantic_fingerprint = ""


def build_intent_vector_index(settings: IntentVectorSettings | None) -> IntentVectorIndex:
    config = IntentVectorConfig(
        enabled=settings.enabled if settings else True,
        distance_threshold=settings.distance_threshold if settings else 0.62,
        timeout_ms=settings.timeout_ms if settings else 350,
        top_k=settings.top_k if settings else 3,
    )
    provider = _provider_from_settings(settings, config)
    return IntentVectorIndex(provider, config)


def _provider_from_settings(
    settings: IntentVectorSettings | None,
    config: IntentVectorConfig,
) -> EmbeddingProvider | None:
    if not config.enabled:
        return None
    provider = (settings.provider if settings else "local").strip().lower()
    dimensions = settings.dimensions if settings else 0
    if provider == "openai":
        if not settings or not settings.base_url or not settings.model:
            return None
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            timeout_ms=config.timeout_ms,
            dimensions=dimensions,
        )
    if provider == "proxy":
        if not settings or not settings.proxy_url:
            return None
        return ProxyEmbeddingProvider(
            url=settings.proxy_url,
            api_key=settings.api_key,
            model=settings.model,
            timeout_ms=config.timeout_ms,
            dimensions=dimensions,
        )
    return LocalHashEmbeddingProvider(dimensions=dimensions)


def _normalize_query(value: str) -> str:
    text = _string(value).lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"(?<!\d)1\d{10}(?!\d)", "{phone}", text)
    text = re.sub(r"(?<![a-z0-9])[0-9x]{15,18}(?![a-z0-9])", "{card}", text)
    text = re.sub(r"第[一二三四五六七八九十0-9]+网格", "{grid}网格", text)
    text = re.sub(r"第[一二三四五六七八九十0-9]+党支部", "{branch}党支部", text)
    text = re.sub(r"叫[\u4e00-\u9fa5]{2,4}(?=的?居民|吗|$)", "叫{name}", text)
    text = re.sub(r"居民[\u4e00-\u9fa5]{2,4}(?=的?(?:信息|资料|档案|详情|具体信息))", "居民{name}", text)
    text = re.sub(r"[\u4e00-\u9fa5]{2,4}(?=负责|党龄|历史走访|个人资料|住户资料)", "{name}", text)
    return text


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    tokens: list[str] = []
    for char in text:
        if not char.isspace():
            tokens.append(char)
    for size in (2, 3, 4):
        if len(text) < size:
            continue
        tokens.extend(text[index : index + size] for index in range(len(text) - size + 1))
    words = re.findall(r"[a-z0-9_{}]+|[\u4e00-\u9fa5]+", text)
    tokens.extend(words)
    return tokens


def _embedding_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    clean_key = _optional_secret(api_key)
    if clean_key:
        headers["Authorization"] = f"Bearer {clean_key}"
    return headers


def _embedding_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    return url if url.endswith("/embeddings") else f"{url}/embeddings"


def _embedding_from_payload(payload: Any) -> list[float]:
    if isinstance(payload, list):
        return [float(value) for value in payload]
    if not isinstance(payload, Mapping):
        raise ValueError("embedding response payload is not an object")
    embedding = payload.get("embedding")
    if isinstance(embedding, list):
        return [float(value) for value in embedding]
    vector = payload.get("vector")
    if isinstance(vector, list):
        return [float(value) for value in vector]
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("embedding"), list):
        return [float(value) for value in data["embedding"]]
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, Mapping) and isinstance(first.get("embedding"), list):
            return [float(value) for value in first["embedding"]]
    raise ValueError("embedding response missing embedding vector")


def _cosine_distance(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return None
    similarity = max(-1.0, min(1.0, dot / (left_norm * right_norm)))
    return 1.0 - similarity


def _vector_tuple(values: Iterable[Any]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(number):
            return ()
        result.append(number)
    return tuple(result)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _string(value)
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any) -> str:
    return str(value or "").strip()


def _optional_secret(value: str | None) -> str:
    text = _string(value)
    if not text or text.casefold() in {"optional", "none", "null", "no", "n/a"}:
        return ""
    if text in {"可选", "无需", "无", "空"}:
        return ""
    return text


def _json_fingerprint(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return ""


def _round_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
