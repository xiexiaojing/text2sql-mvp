from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import GeneratedSql, RejectedQuery
from .semantics import epoch_ms_for_age_at_least, is_set_epoch_ms_sql


@dataclass(frozen=True)
class EntityAttribute:
    name: str
    kind: str
    column: str | None = None
    label: str | None = None
    values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    expression: str | None = None
    group_alias: str | None = None


@dataclass(frozen=True)
class EntitySchema:
    entity_id: str
    table: str
    alias: str
    display_name: str
    attributes: dict[str, EntityAttribute]


class EntityQueryCompiler:
    def __init__(self, schemas: dict[str, EntitySchema]) -> None:
        self.schemas = schemas

    @classmethod
    def from_config(cls, raw: Mapping[str, Any] | None) -> "EntityQueryCompiler":
        schemas: dict[str, EntitySchema] = {}
        for entity_id, item in dict(raw or {}).items():
            if not isinstance(item, Mapping):
                continue
            attributes: dict[str, EntityAttribute] = {}
            for attr_name, attr_item in dict(item.get("attributes") or {}).items():
                if not isinstance(attr_item, Mapping):
                    continue
                attributes[str(attr_name)] = EntityAttribute(
                    name=str(attr_name),
                    kind=str(attr_item.get("kind", "enum")),
                    column=_optional_str(attr_item.get("column")),
                    label=_optional_str(attr_item.get("label")),
                    values={
                        str(value): tuple(str(alias) for alias in aliases or [])
                        for value, aliases in dict(attr_item.get("values") or {}).items()
                    },
                    expression=_optional_str(attr_item.get("expression")),
                    group_alias=_optional_str(attr_item.get("group_alias")),
                )
            schemas[str(entity_id)] = EntitySchema(
                entity_id=str(entity_id),
                table=str(item["table"]),
                alias=str(item.get("alias", entity_id[:1] or "t")),
                display_name=str(item.get("display_name", entity_id)),
                attributes=attributes,
            )
        return cls(schemas)

    def complete_slots(self, question: str, slots: dict[str, Any]) -> None:
        entity_id = str(slots.get("entity") or "")
        if entity_id not in self.schemas:
            return
        query_spec = _as_mapping(slots.get("entity_query"))
        if query_spec:
            slots["entity_query"] = self._normalize_spec(entity_id, query_spec)
            return
        slots["entity_query"] = self._infer_spec(entity_id, question, slots)

    def compile(self, slots: dict[str, Any]) -> GeneratedSql:
        spec = _as_mapping(slots.get("entity_query"))
        entity_id = str(spec.get("entity") or slots.get("entity") or "")
        schema = self.schemas.get(entity_id)
        if schema is None:
            raise RejectedQuery(f"未配置实体属性查询: {entity_id}", "entity_query_not_configured")

        filters = [
            self._normalize_filter(schema, item)
            for item in _as_list(spec.get("filters"))
        ]
        filters = [item for item in filters if item is not None]
        group_by = [
            self._normalize_group_by(schema, item)
            for item in _as_list(spec.get("group_by"))
        ]
        group_by = [item for item in group_by if item is not None]
        order_by = self._normalize_order_by(spec.get("order_by"), has_group=bool(group_by))
        metric = str(spec.get("metric") or "count")
        if metric != "count":
            raise RejectedQuery(f"实体属性查询暂不支持指标: {metric}", "entity_metric_not_allowed")

        params: dict[str, Any] = {}
        select_parts: list[str] = []
        group_parts: list[str] = []
        for field in group_by:
            attr = self._require_attribute(schema, field)
            expr = self._group_expression(schema, attr)
            alias = attr.group_alias or attr.name
            select_parts.append(f"{expr} AS {alias}")
            group_parts.append(alias)
        select_parts.append("COUNT(*) AS total")

        where_parts: list[str] = []
        for item in filters:
            attr = self._require_attribute(schema, str(item["field"]))
            where_parts.append(
                self._filter_sql(schema, attr, str(item.get("op") or "="), item.get("value"), params)
            )

        sql = f"SELECT {', '.join(select_parts)} FROM {schema.table} {schema.alias}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        if group_parts:
            sql += " GROUP BY " + ", ".join(group_parts)
        if order_by:
            sql += " ORDER BY " + ", ".join(order_by)
        if limit := _safe_limit(spec.get("limit")):
            sql += f" LIMIT {limit}"

        return GeneratedSql(
            sql=sql,
            plan=f"动态实体属性查询：{schema.display_name}",
            hit_path="dynamic_entity_query",
            params=params,
            interaction_logs=[
                {
                    "kind": "dynamic_entity_query",
                    "status": "ok",
                    "entity": entity_id,
                    "spec": spec,
                    "paramKeys": sorted(params),
                }
            ],
        )

    def _infer_spec(
        self,
        entity_id: str,
        question: str,
        slots: Mapping[str, Any],
    ) -> dict[str, Any]:
        schema = self.schemas[entity_id]
        filters: list[dict[str, Any]] = []
        group_by: list[str] = []
        order_by: list[dict[str, str]] = []
        limit = None

        for attr in schema.attributes.values():
            completeness = _field_completeness_filter(question, attr)
            if completeness is not None:
                filters.append({"field": attr.name, "op": completeness, "value": True})
                continue
            if attr.kind == "enum":
                value = _enum_value_from_question(question, attr, slots.get(attr.name))
                if value is not None and not _is_group_request(question, attr):
                    filters.append({"field": attr.name, "op": "=", "value": value})
                if _is_group_request(question, attr):
                    group_by.append(attr.name)
            elif attr.kind == "boolean":
                value = _boolean_value_from_question(question, attr, slots.get(attr.name))
                if value is not None and not _is_group_request(question, attr):
                    filters.append({"field": attr.name, "op": "=", "value": value})
                if _is_group_request(question, attr):
                    group_by.append(attr.name)
            elif attr.kind == "age":
                age = _age_from_question(question, slots.get("age"))
                if age is not None:
                    filters.append({"field": attr.name, "op": ">=", "value": age})
            elif attr.kind == "age_group" and _mentions_age_group(question):
                group_by.append(attr.name)
            elif attr.kind == "label_group" and _is_group_request(question, attr):
                group_by.append(attr.name)

        if entity_id == "merchant" and _mentions_rank(question) and "merchant_name" in schema.attributes:
            if "merchant_name" not in group_by:
                group_by.append("merchant_name")
            order_by.append({"field": "total", "direction": "desc"})
        elif _mentions_rank(question):
            order_by.append({"field": "total", "direction": "desc"})
            limit = 1 if "最多" in question else None
        elif group_by:
            order_by.append({"field": "total", "direction": "desc"})

        return self._normalize_spec(
            entity_id,
            {
                "entity": entity_id,
                "metric": "count",
                "filters": filters,
                "group_by": group_by,
                "order_by": order_by,
                "limit": limit,
            },
        )

    def _normalize_spec(self, entity_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
        schema = self.schemas.get(entity_id)
        if schema is None:
            raise RejectedQuery(f"未配置实体属性查询: {entity_id}", "entity_query_not_configured")
        return {
            "entity": entity_id,
            "metric": str(raw.get("metric") or "count"),
            "filters": [
                item
                for item in (
                    self._normalize_filter(schema, value)
                    for value in _as_list(raw.get("filters"))
                )
                if item is not None
            ],
            "group_by": [
                item
                for item in (
                    self._normalize_group_by(schema, value)
                    for value in _as_list(raw.get("group_by") or raw.get("groupBy"))
                )
                if item is not None
            ],
            "order_by": _as_list(raw.get("order_by") or raw.get("orderBy")),
            "limit": _safe_limit(raw.get("limit")),
        }

    def _normalize_filter(
        self,
        schema: EntitySchema,
        raw: Any,
    ) -> dict[str, Any] | None:
        item = _as_mapping(raw)
        if not item:
            return None
        field = str(item.get("field") or item.get("attribute") or "")
        attr = schema.attributes.get(field)
        if attr is None:
            return None
        op = str(item.get("op") or "=")
        value = item.get("value")
        if op in {"filled", "empty"}:
            if not attr.column:
                return None
            return {"field": field, "op": op, "value": value}
        if attr.kind == "enum":
            normalized = _normalize_enum_value(attr, value)
            if normalized is None:
                return None
            value = normalized
            op = "="
        elif attr.kind == "boolean":
            normalized = _normalize_boolean_value(attr, value)
            if normalized is None:
                return None
            value = normalized
            op = "="
        elif attr.kind == "age":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None
            if op not in {">=", ">", "<=", "<", "="}:
                op = ">="
        else:
            return None
        return {"field": field, "op": op, "value": value}

    def _normalize_group_by(self, schema: EntitySchema, raw: Any) -> str | None:
        field = str(raw.get("field") if isinstance(raw, Mapping) else raw)
        attr = schema.attributes.get(field)
        if attr is None:
            return None
        if attr.kind not in {"enum", "boolean", "age_group", "label_group"}:
            return None
        return field

    def _normalize_order_by(self, raw: Any, *, has_group: bool) -> list[str]:
        order_parts: list[str] = []
        for item in _as_list(raw):
            value = _as_mapping(item)
            field = str(value.get("field") or "")
            direction = str(value.get("direction") or "desc").upper()
            if field != "total":
                continue
            if direction not in {"ASC", "DESC"}:
                direction = "DESC"
            order_parts.append(f"total {direction}")
        if has_group and not order_parts:
            order_parts.append("total DESC")
        return order_parts

    def _require_attribute(self, schema: EntitySchema, field: str) -> EntityAttribute:
        attr = schema.attributes.get(field)
        if attr is None:
            raise RejectedQuery(f"字段不在实体属性白名单中: {schema.entity_id}.{field}", "entity_field_not_allowed")
        return attr

    def _group_expression(self, schema: EntitySchema, attr: EntityAttribute) -> str:
        if attr.kind == "age_group":
            if not attr.column:
                raise RejectedQuery(f"年龄段属性缺少源字段: {attr.name}", "entity_field_invalid")
            return _age_group_case(f"{schema.alias}.{attr.column}")
        if attr.kind == "label_group":
            if attr.expression:
                return attr.expression.replace("{alias}", schema.alias)
            if not attr.column:
                raise RejectedQuery(f"分组属性缺少字段: {attr.name}", "entity_field_invalid")
            return f"{schema.alias}.{attr.column}"
        if not attr.column:
            raise RejectedQuery(f"分组属性缺少字段: {attr.name}", "entity_field_invalid")
        return f"{schema.alias}.{attr.column}"

    def _filter_sql(
        self,
        schema: EntitySchema,
        attr: EntityAttribute,
        op: str,
        value: Any,
        params: dict[str, Any],
    ) -> str:
        if op in {"filled", "empty"}:
            if not attr.column:
                raise RejectedQuery(f"属性缺少源字段: {attr.name}", "entity_field_invalid")
            column = f"{schema.alias}.{attr.column}"
            if op == "filled":
                return f"({column} IS NOT NULL AND {column} <> '')"
            return f"({column} IS NULL OR {column} = '')"
        if attr.kind == "age":
            if not attr.column:
                raise RejectedQuery(f"年龄属性缺少源字段: {attr.name}", "entity_field_invalid")
            age = int(value)
            param_name = f"{attr.name}_{len(params)}"
            params[param_name] = epoch_ms_for_age_at_least(age)
            column = f"{schema.alias}.{attr.column}"
            valid = is_set_epoch_ms_sql(column)
            if op in {">=", ">"}:
                return f"{valid} AND {column} <= %({param_name})s"
            if op in {"<", "<="}:
                return f"{valid} AND {column} > %({param_name})s"
            return f"{valid} AND {column} <= %({param_name})s"
        if attr.kind not in {"enum", "boolean"} or not attr.column:
            raise RejectedQuery(f"属性不支持过滤: {attr.name}", "entity_filter_not_allowed")
        param_name = f"{attr.name}_{len(params)}"
        params[param_name] = value
        return f"{schema.alias}.{attr.column} = %({param_name})s"


def _age_group_case(column: str) -> str:
    return (
        "CASE "
        f"WHEN {column} IS NULL OR {column} = 0 THEN '未知' "
        f"WHEN {column} > {epoch_ms_for_age_at_least(18)} THEN '0-17岁' "
        f"WHEN {column} > {epoch_ms_for_age_at_least(35)} THEN '18-34岁' "
        f"WHEN {column} > {epoch_ms_for_age_at_least(60)} THEN '35-59岁' "
        "ELSE '60岁及以上' END"
    )


def _field_completeness_filter(question: str, attr: EntityAttribute) -> str | None:
    label = (attr.label or attr.name or "").strip()
    if not label:
        return None
    empty_markers = ("未填", "没填", "缺失", "缺少", "为空", "未填写", "没有填写", "未录入")
    fill_markers = ("填写", "已填", "填了", "有填", "已填写")
    if label in question:
        if any(marker in question for marker in empty_markers):
            return "empty"
        if any(marker in question for marker in fill_markers):
            return "filled"
    if any(marker in question for marker in fill_markers) and label in question:
        return "filled"
    if any(marker in question for marker in empty_markers) and label in question:
        return "empty"
    return None


def _enum_value_from_question(question: str, attr: EntityAttribute, default: Any = None) -> str | None:
    normalized_default = _normalize_enum_value(attr, default)
    if normalized_default is not None:
        return normalized_default
    for value, aliases in attr.values.items():
        if value and value in question:
            return value
        if any(alias and alias in question for alias in aliases):
            return value
    return None


def _normalize_enum_value(attr: EntityAttribute, raw: Any) -> str | None:
    if raw in (None, "", [], {}):
        return None
    value = str(raw)
    lowered = value.strip().lower()
    for normalized, aliases in attr.values.items():
        if value == normalized or lowered == normalized.lower():
            return normalized
        if any(lowered == alias.lower() for alias in aliases):
            return normalized
    return value


def _boolean_value_from_question(question: str, attr: EntityAttribute, default: Any = None) -> int | None:
    normalized_default = _normalize_boolean_value(attr, default)
    if normalized_default is not None:
        return normalized_default

    false_aliases = _boolean_aliases(attr, expected=0) | {"非本地户籍", "外地户籍", "非本地", "外地", "不是本地"}
    if any(alias and alias in question for alias in false_aliases):
        return 0

    true_aliases = _boolean_aliases(attr, expected=1) | {"本地户籍", "本地", "是"}
    if any(alias and alias in question for alias in true_aliases):
        return 1
    return None


def _normalize_boolean_value(attr: EntityAttribute, raw: Any) -> int | None:
    if raw in (None, "", [], {}):
        return None
    if isinstance(raw, bool):
        return 1 if raw else 0
    if isinstance(raw, int):
        return 1 if raw else 0

    normalized = _normalize_enum_value(attr, raw)
    value = str(normalized if normalized is not None else raw).strip().lower()
    if value in {"1", "true", "yes", "y", "是", "本地", "本地户籍", "local"}:
        return 1
    if value in {"0", "false", "no", "n", "否", "非本地", "非本地户籍", "外地", "外地户籍"}:
        return 0
    return None


def _boolean_aliases(attr: EntityAttribute, *, expected: int) -> set[str]:
    aliases: set[str] = set()
    expected_keys = {"1", "true"} if expected else {"0", "false"}
    for value, value_aliases in attr.values.items():
        if str(value).strip().lower() in expected_keys:
            aliases.update(value_aliases)
    return aliases


def _age_from_question(question: str, default: Any = None) -> int | None:
    try:
        if default not in (None, ""):
            return int(default)
    except (TypeError, ValueError):
        pass
    match = re.search(r"(\d{2,3})\s*岁", question)
    if match:
        return int(match.group(1))
    if any(keyword in question for keyword in ["高龄", "80岁", "80 岁"]):
        return 80
    if any(keyword in question for keyword in ["老人", "老年人", "60岁", "60 岁"]):
        return 60
    return None


def _is_group_request(question: str, attr: EntityAttribute) -> bool:
    label = attr.label or attr.name
    group_words = ["各", "按", "分布", "统计", "排名", "排行"]
    if label and any(f"{word}{label}" in question or f"{label}{word}" in question for word in group_words):
        return True
    if attr.name == "sexual" and any(keyword in question for keyword in ["按性别", "各性别", "性别分布"]):
        return True
    if attr.name == "marital_status" and any(keyword in question for keyword in ["按婚姻", "各婚姻", "婚姻状态分布"]):
        return True
    if attr.name == "party_branch_name" and any(keyword in question for keyword in ["党员数量排名", "党组织排名", "各党支部", "各党组织"]):
        return True
    return False


def _mentions_age_group(question: str) -> bool:
    return any(keyword in question for keyword in ["各年龄段", "年龄段", "年龄分布", "年龄结构"])


def _mentions_rank(question: str) -> bool:
    return any(keyword in question for keyword in ["排名", "排行", "最多", "TOP", "top"])


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", {}):
        return []
    return value if isinstance(value, list) else [value]


def _safe_limit(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(parsed, 1000))


def _optional_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
