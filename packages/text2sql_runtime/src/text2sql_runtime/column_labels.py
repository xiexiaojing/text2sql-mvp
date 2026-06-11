from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import load_yaml
from .schema import SchemaCatalog

# 统计/别名列：SQL 中 AS 出来的字段，白名单物理列里没有对应项
COMMON_ALIAS_LABELS: dict[str, str] = {
    "total": "数量",
    "count": "数量",
    "age_group": "年龄段",
    "sexual": "性别",
    "sexual_value": "性别",
    "political_status_value": "政治面貌",
    "political_status": "政治面貌",
    "party_branch_label": "党组织",
    "marital_status": "婚姻状态",
    "local_household": "本地户籍",
    "household_size": "家庭人数",
    "disabled_count": "残疾人数量",
    "target_name": "姓名",
    "address_path": "居住地址",
    "building_name": "楼栋",
    "building_name_path": "楼栋路径",
    "grid_name": "网格名称",
    "grid_id": "网格ID",
    "manager_name": "负责人",
    "community_name": "社区名称",
    "tag_name": "标签名称",
    "party_branch_name": "党支部名称",
    "matched_party_branch_name": "匹配党支部",
    "responsibility_role": "负责角色",
    "elderly_count": "老年人数量",
    "resident_count": "居民数量",
    "elderly_ratio": "老年人占比",
    "elderly_party_count": "老年党员数量",
    "elderly_party_ratio": "老年党员占比",
    "volunteer_party_count": "志愿者党员数量",
    "party_count": "党员数量",
    "volunteer_party_ratio": "志愿者党员占比",
    "house_node_total_count": "房屋总数",
    "house_node_update_count": "已更新户数",
    "house_node_update_rate": "台账更新率",
    "current_vacant_count": "当前空房数",
    "this_year_new_vacant_count": "今年新增空房",
    "last_year_new_vacant_count": "去年新增空房",
    "last_year_carryover_count": "去年结转空房",
    "visit_hour": "走访时段",
    "visit_month": "走访月份",
    "visit_target": "走访对象",
    "visit_resident_name": "被走访居民姓名",
    "visit_person_name": "走访人姓名",
    "visit_person_id": "走访人ID",
    "visit_when": "走访时间",
    "visit_when_value": "走访时间",
    "visit_way": "走访方式",
    "visit_where": "走访地点",
    "visit_what": "走访对象",
    "colleague_name": "同事姓名",
    "risk_title": "风险事项",
    "status_value": "状态",
    "channel_value": "渠道",
    "merchant_name": "商户名称",
    "refund_date": "退款日期",
    "category_value": "分类",
    "question_namef": "诉求分类",
    "leave_time_text": "离职时间",
    "previous_departments": "原部门",
    "node_level": "节点层级",
    "latest_update_time": "最后更新时间",
    "department_path": "部门路径",
    "residence_status_desc": "居住状况",
    "name": "姓名",
    "mobile": "手机号",
    "contact_mobile": "联系电话",
    "born_at": "出生时间",
    "remarks": "备注",
    "duty": "职务",
    "job": "岗位",
    "address": "地址",
    "house_status": "房屋状态",
    "housing_property": "房屋性质",
    "id": "ID",
}


class EntityColumnLabelIndex:
    def __init__(self, alias_labels: dict[str, str]) -> None:
        self._alias_labels = alias_labels

    @classmethod
    def from_entity_query_schemas(cls, raw: Mapping[str, Any] | None) -> EntityColumnLabelIndex:
        labels: dict[str, str] = {}
        for entity_item in dict(raw or {}).values():
            if not isinstance(entity_item, Mapping):
                continue
            for attr_name, attr_item in dict(entity_item.get("attributes") or {}).items():
                if not isinstance(attr_item, Mapping):
                    continue
                label = str(attr_item.get("label") or attr_name)
                attr_key = str(attr_name).lower()
                labels[attr_key] = label
                labels[f"{attr_key}_value"] = label
                group_alias = attr_item.get("group_alias")
                if group_alias:
                    labels[str(group_alias).lower()] = label
                column = attr_item.get("column")
                if column:
                    labels[str(column).lower()] = label
        return cls(labels)

    @classmethod
    def from_business_semantics_path(cls, path: Path) -> EntityColumnLabelIndex:
        if not path.exists():
            return cls({})
        raw = load_yaml(path)
        return cls.from_entity_query_schemas(raw.get("entity_query_schemas"))

    def get(self, column: str) -> str | None:
        return self._alias_labels.get(column.lower())


def resolve_column_display_labels(
    columns: list[str],
    catalog: SchemaCatalog | None,
    sql_tables: list[str] | None = None,
    entity_labels: EntityColumnLabelIndex | None = None,
) -> list[str]:
    if not columns:
        return []

    preferred: dict[str, str] = {}
    global_map: dict[str, str] = {}

    if catalog is not None:
        if sql_tables:
            for table_name in dict.fromkeys(sql_tables):
                table = catalog.get(table_name)
                if not table:
                    continue
                for column in table.columns.values():
                    if column.display_name:
                        preferred[column.name.lower()] = str(column.display_name)

        for table in catalog.tables:
            for column in table.columns.values():
                key = column.name.lower()
                if column.display_name and key not in global_map:
                    global_map[key] = str(column.display_name)

    labels: list[str] = []
    for column in columns:
        key = column.lower()
        entity_label = entity_labels.get(column) if entity_labels is not None else None
        label = (
            preferred.get(key)
            or entity_label
            or global_map.get(key)
            or COMMON_ALIAS_LABELS.get(key)
            or column
        )
        labels.append(label)
    return labels
