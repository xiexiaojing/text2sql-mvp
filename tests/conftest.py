from __future__ import annotations

from pathlib import Path

import pytest
from text2sql_runtime.audit import SQLiteAuditStore
from text2sql_runtime.config import load_settings
from text2sql_runtime.executor import DryRunExecutor
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.semantics import SemanticIndex
from text2sql_runtime.service import Text2SqlService


@pytest.fixture()
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def service(project_root: Path, tmp_path: Path) -> Text2SqlService:
    settings = load_settings(project_root)
    settings = type(settings)(
        project_root=settings.project_root,
        execution_mode="dry_run",
        executor_backend="direct_mysql",
        allow_sensitive_fields=True,
        audit_db_path=tmp_path / "audit.sqlite3",
        mysql=settings.mysql,
        mysql_mcp=settings.mysql_mcp,
        llm=type(settings.llm)(
            base_url=None,
            api_key=None,
            model=settings.llm.model,
            timeout_seconds=settings.llm.timeout_seconds,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        ),
        intent_vector=settings.intent_vector,
        performance=settings.performance,
    )
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    return Text2SqlService(
        settings=settings,
        catalog=catalog,
        semantics=semantics,
        executor=DryRunExecutor(),
        audit_store=SQLiteAuditStore(settings.audit_db_path),
    )


@pytest.fixture()
def service_with_sensitive_fields(project_root: Path, tmp_path: Path) -> Text2SqlService:
    settings = load_settings(project_root)
    settings = type(settings)(
        project_root=settings.project_root,
        execution_mode="dry_run",
        executor_backend="direct_mysql",
        allow_sensitive_fields=True,
        audit_db_path=tmp_path / "audit.sqlite3",
        mysql=settings.mysql,
        mysql_mcp=settings.mysql_mcp,
        llm=type(settings.llm)(
            base_url=None,
            api_key=None,
            model=settings.llm.model,
            timeout_seconds=settings.llm.timeout_seconds,
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
        ),
        intent_vector=settings.intent_vector,
        performance=settings.performance,
    )
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    return Text2SqlService(
        settings=settings,
        catalog=catalog,
        semantics=semantics,
        executor=DryRunExecutor(),
        audit_store=SQLiteAuditStore(settings.audit_db_path),
    )
