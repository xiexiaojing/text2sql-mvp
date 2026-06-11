from __future__ import annotations

from .models import EstimateResult, RejectedQuery
from .schema import SchemaCatalog
from .semantics import SemanticIndex


class QueryRouter:
    def __init__(
        self,
        catalog: SchemaCatalog,
        semantics: SemanticIndex,
        performance: dict,
        allow_sensitive_fields: bool = False,
    ) -> None:
        self.catalog = catalog
        self.semantics = semantics
        self.performance = performance
        self.allow_sensitive_fields = allow_sensitive_fields

    def estimate_question(self, question: str, domain_id: str | None) -> EstimateResult:
        self.validate_question_policies(question, domain_id)
        concepts = self.semantics.detect(question)
        candidate_tables = self._candidate_tables(question, concepts)
        estimated_seconds = self._estimate_seconds(question, candidate_tables)
        if estimated_seconds > 30:
            raise RejectedQuery("预计超过 30 秒，请使用智能表单/离线统计", "estimated_timeout")

        hit_path = "semantic_rule" if concepts else "guarded_text2sql"
        warnings = []
        if estimated_seconds > 20:
            warnings.append("预计为复杂统计，可能超过 20 秒")
        return EstimateResult(
            status="accepted",
            hit_path=hit_path,
            estimated_seconds=estimated_seconds,
            candidate_tables=candidate_tables,
            warnings=warnings,
        )

    def estimate_tables(
        self,
        question: str,
        domain_id: str | None,
        candidate_tables: list[str],
        hit_path: str,
    ) -> EstimateResult:
        self.validate_question_policies(question, domain_id)
        estimated_seconds = self._estimate_seconds(question, candidate_tables)
        if estimated_seconds > 30:
            raise RejectedQuery("预计超过 30 秒，请使用智能表单/离线统计", "estimated_timeout")
        warnings = []
        if estimated_seconds > 20:
            warnings.append("预计为复杂统计，可能超过 20 秒")
        return EstimateResult(
            status="accepted",
            hit_path=hit_path,
            estimated_seconds=estimated_seconds,
            candidate_tables=sorted(set(candidate_tables)),
            warnings=warnings,
        )

    def validate_question_policies(
        self,
        question: str,
        domain_id: str | None,
        *,
        include_sensitive: bool = True,
    ) -> None:
        if not domain_id:
            raise RejectedQuery("缺少 domainId，无法注入权限范围", "missing_domain_id")
        if any(keyword in question for keyword in ["导出", "下载", "全部明细", "所有明细"]):
            raise RejectedQuery("明细导出或全量明细请使用智能表单/离线统计", "detail_export")
        if any(keyword in question for keyword in ["所有社区", "全市", "跨社区", "跨域"]):
            raise RejectedQuery("第一版只支持当前 domainId 范围查询", "cross_domain")

    def estimate_sql(self, sql_tables: list[str]) -> float:
        limits = self.performance.get("limits", {})
        rows_per_second = int(limits.get("rows_per_second_estimate", 50000))
        unique_tables = sorted(set(sql_tables))
        rows = sum(
            (self.catalog.get(table).row_count_estimate if self.catalog.get(table) else 0)
            for table in unique_tables
        )
        join_penalty = max(0, len(unique_tables) - 1) * 1.5
        return (rows / rows_per_second) + join_penalty

    def reject_if_sql_too_complex(self, sql_tables: list[str]) -> None:
        limits = self.performance.get("limits", {})
        max_tables = int(limits.get("max_tables_per_query", 6))
        unique_tables = sorted(set(sql_tables))
        if len(unique_tables) > max_tables:
            raise RejectedQuery("关联表过多，请改用预聚合/离线统计", "too_many_tables")
        estimated = self.estimate_sql(sql_tables)
        if estimated > 30:
            raise RejectedQuery("预计超过 30 秒，请使用智能表单/离线统计", "estimated_timeout")

    def reject_if_explain_too_large(self, scanned_rows: int) -> None:
        reject_rows = int(self.performance.get("limits", {}).get("reject_scan_rows", 500000))
        if scanned_rows > reject_rows:
            raise RejectedQuery("EXPLAIN 预估扫描行数过大，请使用智能表单/离线统计", "explain_too_large")

    def _candidate_tables(self, question: str, concepts) -> list[str]:
        tables: list[str] = []
        for concept in concepts:
            if concept.rule.get("table"):
                tables.append(str(concept.rule["table"]))
            elif concept.object_name:
                table = self.catalog.by_object(concept.object_name)
                if table:
                    tables.append(table.name)
        keyword_map = {
            "订单": "payment_order",
            "支付": "payment_order",
            "退款": "refund_order",
            "商户": "merchant",
            "商家": "merchant",
        }
        for keyword, table in keyword_map.items():
            if keyword in question and table in self.catalog.table_names:
                tables.append(table)
        if not tables:
            tables.append("payment_order")
        return sorted(set(tables))

    def _estimate_seconds(self, question: str, candidate_tables: list[str]) -> float:
        rows = sum(
            (self.catalog.get(table).row_count_estimate if self.catalog.get(table) else 50000)
            for table in candidate_tables
        )
        rows_per_second = int(self.performance.get("limits", {}).get("rows_per_second_estimate", 50000))
        estimate = rows / rows_per_second
        if any(keyword in question for keyword in ["排名", "分组", "按", "趋势", "同比", "环比"]):
            estimate += 2.0
        if len(candidate_tables) > 2:
            estimate += (len(candidate_tables) - 2) * 1.5
        if any(keyword in question for keyword in ["全年", "三年", "历史", "所有"]):
            estimate += 10
        return round(estimate, 2)
