from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from text2sql_runtime.audit import SQLiteAuditStore
from text2sql_runtime.business_semantics import BusinessSemanticIndex, resolve_business_semantics_path
from text2sql_runtime.config import load_settings
from text2sql_runtime.executor import DryRunExecutor
from text2sql_runtime.field_encryption import FieldEncryptionSettings
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.semantics import SemanticIndex
from text2sql_runtime.service import Text2SqlService


def _dry_run_settings(settings, tmp_path: Path):
    return replace(
        settings,
        execution_mode="dry_run",
        executor_backend="direct_mysql",
        allow_sensitive_fields=True,
        audit_db_path=tmp_path / "audit.sqlite3",
        llm=replace(
            settings.llm,
            base_url=None,
            api_key=None,
        ),
    )


@pytest.fixture()
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def service(project_root: Path, tmp_path: Path) -> Text2SqlService:
    settings = _dry_run_settings(load_settings(project_root), tmp_path)
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
    settings = _dry_run_settings(load_settings(project_root), tmp_path)
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
def service_with_field_encryption(project_root: Path, tmp_path: Path) -> Text2SqlService:
    settings = replace(
        _dry_run_settings(load_settings(project_root), tmp_path),
        field_encryption=FieldEncryptionSettings(enabled=True, encryption_type="sm4"),
    )
    business_semantics = BusinessSemanticIndex.from_config(
        resolve_business_semantics_path(project_root),
        vector_settings=settings.intent_vector,
        llm_settings=settings.llm,
        field_encryption=settings.field_encryption,
    )
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    return Text2SqlService(
        settings=settings,
        catalog=catalog,
        semantics=semantics,
        business_semantics=business_semantics,
        executor=DryRunExecutor(),
        audit_store=SQLiteAuditStore(settings.audit_db_path),
    )
