from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .column_labels import EntityColumnLabelIndex, resolve_column_display_labels
from .display_columns import filter_public_table
from .models import ExecutionResult
from .schema import SchemaCatalog

_COUNT_ALIAS_RE = re.compile(r"\bas\s+count\b", re.IGNORECASE)

_INTENT_SCALAR_ANSWERS: dict[str, str] = {
    "payment_order_count": "支付订单共 {value} 笔。",
    "merchant_count": "商户共 {value} 家。",
}


class ResultFormatter:
    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        entity_labels: EntityColumnLabelIndex | None = None,
    ) -> None:
        self.catalog = catalog
        self.entity_labels = entity_labels

    def format(
        self,
        execution: ExecutionResult,
        sql: str,
        sql_tables: list[str] | None = None,
        *,
        question: str | None = None,
        intent_id: str | None = None,
        display_name: str | None = None,
        output_type: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if execution.mode == "dry_run":
            return (
                "SQL 已生成并通过安全校验；当前未配置真实 MySQL，只返回执行计划。",
                {"columns": [], "rows": [], "row_count": 0, "mode": "dry_run"},
            )
        column_labels = resolve_column_display_labels(
            execution.columns,
            self.catalog,
            sql_tables,
            self.entity_labels,
        )
        omit_table = _should_omit_scalar_table(execution.rows, output_type)
        if omit_table:
            table: dict[str, Any] = {
                "columns": [],
                "column_labels": [],
                "rows": [],
                "row_count": 0,
                "mode": execution.mode,
            }
        else:
            table = filter_public_table(
                {
                    "columns": execution.columns,
                    "column_labels": column_labels,
                    "rows": [_format_row_display_values(row) for row in execution.rows],
                    "row_count": len(execution.rows),
                    "mode": execution.mode,
                }
            )
        answer = self._answer_from_rows(
            execution.rows,
            sql,
            question=question,
            intent_id=intent_id,
            display_name=display_name,
            output_type=output_type,
        )
        return answer, table

    def _answer_from_rows(
        self,
        rows: list[dict[str, Any]],
        sql: str,
        *,
        question: str | None = None,
        intent_id: str | None = None,
        display_name: str | None = None,
        output_type: str | None = None,
    ) -> str:
        if not rows:
            if "manager_name" in sql.lower() and "grid_manager_relation" in sql.lower():
                return "未找到该地址对应的房屋，或暂未关联网格负责人。"
            if "house_status" in sql.lower() or "housing_property" in sql.lower():
                return "未找到该地址对应的房屋，无法判断是否为出租房。"
            if "responsibility_role" in sql.lower():
                return "未找到该员工负责的网格，或负责网格下暂无符合条件的老年人。"
            if "building_name" in sql.lower() and "group by" in sql.lower():
                if "temp_resident" in sql.lower():
                    return "当前社区暂无流动人口记录，无法按楼栋排行。"
                return "当前社区暂无空置房，无法按楼栋排行。"
            if "contact_mobile" in sql.lower() and "join resident r2" in sql.lower():
                return "当前社区未发现重复联系手机号的居民。"
            if "visit_person_name" in sql.lower() and "visit_when" in sql.lower():
                if "born_at" in sql.lower() and "visit_resident_name" not in sql.lower():
                    return "今年暂无已完成走访的高龄老人记录。"
                return "未找到该居民或对应家庭的走访记录。"
            return "查询完成，未返回记录。"
        first = rows[0]
        if "household_size" in first:
            return _answer_household_disability(rows)
        if "building_name" in first and "total" in first:
            if "temp_resident" in sql.lower():
                return _answer_floating_population_building_top(rows)
            return _answer_vacant_house_building_rank(rows)
        if "house_status" in first or "housing_property" in first:
            return _answer_house_rental_status(rows)
        if "manager_name" in first and "grid_name" in first:
            return _answer_address_grid_manager(rows)
        if "building_name" in first and "building_name_path" in first and "total" not in first:
            return _answer_grid_building_list(rows)
        if "responsibility_role" in first:
            return _answer_responsible_elderly_list(rows)
        if "house_node_update_rate" in first:
            return _answer_ledger_update_rate_grid(rows)
        if "this_year_new_vacant_count" in first:
            return _answer_vacant_house_growth(rows)
        if "mobile" in first and "name" in first and "total" not in first:
            return _answer_duplicate_mobile_list(rows)
        if "grid_name" in first and "total" in first and "building_name" not in first:
            return _answer_grid_count_distribution(rows)
        if len(rows) == 1 and "total" in first:
            return _answer_scalar_count(
                first["total"],
                question=question,
                intent_id=intent_id,
                display_name=display_name,
                output_type=output_type,
            )
        if len(rows) == 1 and "latest_update_time" in first:
            formatted = _format_timestamp(first.get("latest_update_time"))
            if formatted == "暂无":
                return "暂无台账更新记录。"
            return f"最后更新时间为 {formatted}。"
        if "visit_person_name" in first and "visit_when" in first:
            if "name" in first and "born_at" in first and "visit_resident_name" not in first:
                return _answer_visiting_senior_visited_list(rows)
            return _answer_visiting_last_visitor(rows)
        if _COUNT_ALIAS_RE.search(sql) and len(rows) == 1:
            value = next(iter(first.values()))
            return _answer_scalar_count(
                value,
                question=question,
                intent_id=intent_id,
                display_name=display_name,
                output_type=output_type,
            )
        return f"查询完成，返回 {len(rows)} 行。"


def _answer_visiting_senior_visited_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "今年暂无已完成走访的高龄老人记录。"
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        resident_id = str(row.get("id") or row.get("name") or "").strip()
        if not resident_id:
            continue
        if resident_id not in seen:
            seen[resident_id] = row
    if not seen:
        return "今年暂无已完成走访的高龄老人记录。"
    names = [str(row.get("name") or "").strip() for row in seen.values() if row.get("name")]
    preview = "、".join(names[:12])
    if len(names) > 12:
        preview = f"{preview} 等"
    return f"今年已走访高龄老人 {len(seen)} 位：{preview}。"


def _answer_visiting_last_visitor(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到该居民或对应家庭的走访记录。"
    row = rows[0]
    visitor = str(row.get("visit_person_name") or "").strip()
    if not visitor:
        return "找到了走访记录，但未登记走访人姓名。"
    resident = str(row.get("visit_resident_name") or row.get("visit_what") or "该对象").strip()
    when = _format_timestamp(row.get("visit_when"))
    way = str(row.get("visit_way") or "").strip()
    parts = [f"上次走访{resident}的是 {visitor}"]
    if when != "暂无":
        parts.append(f"走访时间 {when}")
    if way:
        parts.append(f"方式 {way}")
    return "，".join(parts) + "。"


def _answer_duplicate_mobile_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前社区未发现重复联系手机号的居民。"
    by_phone: dict[str, list[str]] = {}
    for row in rows:
        phone = str(row.get("mobile") or "").strip()
        name = str(row.get("name") or "").strip()
        if not phone or not name:
            continue
        by_phone.setdefault(phone, []).append(name)
    parts: list[str] = []
    for phone, names in sorted(by_phone.items(), key=lambda item: (-len(item[1]), item[0])):
        parts.append(f"{phone}：{'、'.join(names)}")
    preview = "；".join(parts[:8])
    if len(parts) > 8:
        preview = f"{preview} 等"
    return (
        f"共 {len(rows)} 位居民存在重复联系手机号，涉及 {len(by_phone)} 个号码：{preview}。"
    )


def _answer_household_disability(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到该居民的居住地址，无法统计家庭成员。"
    row = rows[0]
    if len(rows) > 1:
        row = min(rows, key=lambda item: int(item.get("household_size") or 999999))
    name = str(row.get("target_name") or "该居民")
    size = int(row.get("household_size") or 0)
    disabled = int(row.get("disabled_count") or 0)
    address = str(row.get("address_path") or "").strip()
    if disabled > 0:
        detail = f"其中有 {disabled} 位残疾人"
    else:
        detail = "没有登记残疾人"
    answer = f"{name}家共 {size} 口人，{detail}。"
    if address:
        return f"（{address}）{answer}"
    return answer


def _answer_vacant_house_growth(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "暂无空房数据，无法计算增长率。"
    row = rows[0]
    current = int(row.get("current_vacant_count") or 0)
    this_year = int(row.get("this_year_new_vacant_count") or 0)
    last_year = int(row.get("last_year_new_vacant_count") or 0)
    carryover = int(row.get("last_year_carryover_count") or 0)
    if last_year <= 0:
        if this_year <= 0:
            return f"今年与去年均无新增空房记录；当前空房存量 {current} 套。"
        return (
            f"去年无新增空房，今年新增 {this_year} 套（当前空房存量 {current} 套，"
            f"其中 {carryover} 套在去年末前已标记为空房）。"
        )
    growth_pct = (this_year - last_year) / last_year * 100
    direction = "增长" if growth_pct >= 0 else "下降"
    return (
        f"今年新增空房 {this_year} 套，去年 {last_year} 套，同比{direction} {abs(growth_pct):.1f}%；"
        f"当前空房存量 {current} 套。"
    )


def _answer_floating_population_building_top(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前社区暂无流动人口记录，无法按楼栋排行。"
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        building_name = str(row.get("building_name") or "未知楼栋")
        total = row.get("total")
        parts.append(f"{index}. {building_name}：{total} 人")
    return f"流动人口 TOP3 楼栋：{'；'.join(parts)}。"


def _answer_vacant_house_building_rank(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前社区暂无空置房，无法按楼栋排行。"
    parts: list[str] = []
    for index, row in enumerate(rows[:10], start=1):
        building_name = str(row.get("building_name") or "未知楼栋")
        total = row.get("total")
        parts.append(f"{index}. {building_name}：{total} 套")
    if len(rows) > 10:
        parts.append(f"……共 {len(rows)} 个楼栋")
    return f"空置房按楼栋排行：{'；'.join(parts)}。"


def _answer_grid_count_distribution(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "各网格暂无符合条件的数据。"
    total_people = sum(int(row.get("total") or 0) for row in rows)
    parts: list[str] = []
    for index, row in enumerate(rows[:12], start=1):
        grid_name = str(row.get("grid_name") or "未知网格")
        count = int(row.get("total") or 0)
        parts.append(f"{index}. {grid_name}：{count} 人")
    if len(rows) > 12:
        parts.append(f"……共 {len(rows)} 个网格")
    if len(rows) == 1:
        only = rows[0]
        grid_name = str(only.get("grid_name") or "未知网格")
        count = int(only.get("total") or 0)
        return f"仅「{grid_name}」有 {count} 人，其余网格暂无匹配记录。"
    return f"各网格合计 {total_people} 人，分布如下：{'；'.join(parts)}。"


def _answer_ledger_update_rate_grid(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "去年各网格暂无台账更新率数据。"
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        grid_name = str(row.get("grid_name") or "未知网格")
        rate = row.get("house_node_update_rate")
        updated = row.get("house_node_update_count")
        total = row.get("house_node_total_count")
        if rate is None:
            parts.append(f"{index}. {grid_name}")
            continue
        try:
            rate_pct = float(rate) * 100
        except (TypeError, ValueError):
            rate_pct = 0.0
        parts.append(
            f"{index}. {grid_name}：更新率 {rate_pct:.1f}%（{updated}/{total} 户）"
        )
    return f"去年台账更新率排名：{'；'.join(parts)}。"


def _answer_grid_building_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "该网格暂未关联楼栋。"
    grid_name = str(rows[0].get("grid_name") or "该网格")
    buildings: list[str] = []
    seen: set[str] = set()
    for row in rows:
        building_name = str(row.get("building_name") or "").strip()
        building_path = str(row.get("building_name_path") or "").strip()
        label = building_name or building_path
        if not label or label in seen:
            continue
        seen.add(label)
        buildings.append(label)
    if not buildings:
        return f"「{grid_name}」暂未解析到楼栋名称。"
    preview = "、".join(buildings[:20])
    if len(buildings) > 20:
        preview = f"{preview} 等"
    return f"「{grid_name}」包括 {len(buildings)} 栋楼：{preview}。"


def _answer_address_grid_manager(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到该地址对应的房屋，或暂未关联网格负责人。"
    if len(rows) == 1:
        row = rows[0]
        grid_name = str(row.get("grid_name") or "未知网格")
        manager_name = str(row.get("manager_name") or "未知")
        duty = str(row.get("duty") or row.get("job") or "").strip()
        duty_text = f"（{duty}）" if duty else ""
        name_path = str(row.get("name_path") or "").strip()
        if name_path:
            return f"「{name_path}」所属网格为「{grid_name}」，负责人 {manager_name}{duty_text}。"
        return f"该地址所属网格为「{grid_name}」，负责人 {manager_name}{duty_text}。"
    parts: list[str] = []
    for index, row in enumerate(rows[:8], start=1):
        grid_name = str(row.get("grid_name") or "未知网格")
        manager_name = str(row.get("manager_name") or "未知")
        name_path = str(row.get("name_path") or grid_name)
        parts.append(f"{index}. {name_path} → {grid_name} / {manager_name}")
    if len(rows) > 8:
        parts.append(f"……共 {len(rows)} 条")
    return f"共匹配 {len(rows)} 条地址记录：{'；'.join(parts)}。"


def _answer_responsible_elderly_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到该员工负责的网格，或负责网格下暂无符合条件的老年人。"
    grid_names = sorted({str(row.get("grid_name") or "").strip() for row in rows if row.get("grid_name")})
    grid_text = "、".join(grid_names) if grid_names else "相关网格"
    names = [str(row.get("name") or "").strip() for row in rows if row.get("name")]
    preview = "、".join(names[:8])
    if len(names) > 8:
        preview = f"{preview} 等"
    return f"共 {len(rows)} 位老年人（负责网格：{grid_text}）：{preview}。"


def _answer_house_rental_status(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "未找到该地址对应的房屋，无法判断是否为出租房。"
    row = rows[0]
    if len(rows) > 1:
        row = min(rows, key=lambda item: len(str(item.get("name_path") or "")))
    name_path = str(row.get("name_path") or row.get("name") or "该地址")
    house_status = str(row.get("house_status") or "").strip()
    housing_property = str(row.get("housing_property") or "").strip()
    status_text = "、".join(item for item in [house_status, housing_property] if item) or "未登记"
    combined = f"{house_status}{housing_property}"
    if any(token in combined for token in ("出租", "租赁", "租户", "中介", "直租")):
        verdict = "是出租房"
    elif "自住" in combined:
        verdict = "不是出租房（当前登记为自住）"
    elif "空置" in combined or "空房" in combined:
        verdict = "当前为空置房，不是出租房"
    else:
        verdict = "暂无法明确判定为出租房，请结合现场台账确认"
    return f"{name_path} 的房屋状态为 {status_text}，{verdict}。"


def _is_timestamp_column(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith(("_time", "_at", "_when"))


def _looks_like_epoch(value: Any) -> bool:
    if isinstance(value, str):
        return False
    if isinstance(value, dt.datetime):
        return True
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    return number >= 1_000_000_000_000 or 1_000_000_000 <= number < 10_000_000_000


def _format_row_display_values(row: dict[str, Any]) -> dict[str, Any]:
    formatted = dict(row)
    for key, value in row.items():
        if _is_timestamp_column(key) and _looks_like_epoch(value):
            formatted[key] = _format_timestamp(value)
    return formatted


def _format_timestamp(value: Any) -> str:
    if value is None:
        return "暂无"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    try:
        ms = int(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or "暂无"
    if ms <= 0:
        return "暂无"
    if ms < 10_000_000_000:
        ms *= 1000
    try:
        moment = dt.datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return str(value)
    if moment.year < 1970 or moment.year > 2100:
        return str(value)
    return moment.strftime("%Y/%m/%d %H:%M")


def _format_scalar_value(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_scalar_count_row(row: dict[str, Any]) -> bool:
    if len(row) != 1:
        return False
    value = next(iter(row.values()))
    if value is None:
        return True
    text = str(value).strip()
    return text.isdigit() or (text.replace(".", "", 1).isdigit() and text.count(".") <= 1)


def _should_omit_scalar_table(rows: list[dict[str, Any]], output_type: str | None) -> bool:
    if output_type == "scalar_count" and len(rows) == 1:
        return True
    if len(rows) == 1 and _is_scalar_count_row(rows[0]):
        return True
    return False


def _answer_scalar_count(
    value: Any,
    *,
    question: str | None,
    intent_id: str | None,
    display_name: str | None,
    output_type: str | None,
) -> str:
    formatted_value = _format_scalar_value(value)
    if intent_id and intent_id in _INTENT_SCALAR_ANSWERS:
        return _INTENT_SCALAR_ANSWERS[intent_id].format(value=formatted_value)
    normalized_question = (question or "").strip()
    if "支付订单" in normalized_question or "订单" in normalized_question:
        return f"支付订单共 {formatted_value} 笔。"
    if "商户" in normalized_question:
        return f"商户共 {formatted_value} 家。"
    if display_name:
        return f"{display_name}为 {formatted_value}。"
    if output_type == "scalar_count":
        return f"共 {formatted_value}。"
    return f"查询结果为 {formatted_value}。"
