from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MySqlSettings:
    host: str | None
    port: int
    database: str | None
    user: str | None
    password: str | None
    ssl_disabled: bool = False
    connect_timeout_seconds: int = 5
    read_timeout_seconds: int = 35

    @property
    def configured(self) -> bool:
        return bool(self.host and self.database and self.user and self.password)


@dataclass(frozen=True)
class LlmSettings:
    base_url: str | None
    api_key: str | None
    model: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    transport: str = "openai"
    policy: str = "auto"

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class MySqlMcpSettings:
    command: str
    args: list[str]
    connection_name: str
    database: str | None
    timeout_seconds: int


@dataclass(frozen=True)
class IntentVectorSettings:
    enabled: bool
    provider: str
    distance_threshold: float
    timeout_ms: int
    top_k: int
    base_url: str | None
    api_key: str | None
    model: str | None
    dimensions: int
    proxy_url: str | None


@dataclass(frozen=True)
class RuntimeSettings:
    project_root: Path
    execution_mode: str
    executor_backend: str
    allow_sensitive_fields: bool
    audit_db_path: Path
    mysql: MySqlSettings
    mysql_mcp: MySqlMcpSettings
    llm: LlmSettings
    intent_vector: IntentVectorSettings
    performance: dict[str, Any]

    @property
    def live_execution(self) -> bool:
        if self.execution_mode == "live":
            return True
        if self.execution_mode == "dry_run":
            return False
        return self.mysql.configured


def default_project_root() -> Path:
    env_root = os.getenv("TEXT2SQL_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return data


def load_settings(project_root: Path | None = None) -> RuntimeSettings:
    root = project_root or default_project_root()
    load_local_env(root / ".env.local")
    performance = load_yaml(root / "configs" / "performance.yaml")
    audit_path = Path(os.getenv("TEXT2SQL_AUDIT_DB", str(root / "data" / "audit.sqlite3")))
    mysql = MySqlSettings(
        host=os.getenv("TEXT2SQL_MYSQL_HOST"),
        port=int(os.getenv("TEXT2SQL_MYSQL_PORT", "3306")),
        database=os.getenv("TEXT2SQL_MYSQL_DATABASE"),
        user=os.getenv("TEXT2SQL_MYSQL_USER"),
        password=os.getenv("TEXT2SQL_MYSQL_PASSWORD"),
        ssl_disabled=_env_bool("TEXT2SQL_MYSQL_SSL_DISABLED", False),
        connect_timeout_seconds=int(os.getenv("TEXT2SQL_MYSQL_CONNECT_TIMEOUT", "5")),
        read_timeout_seconds=int(os.getenv("TEXT2SQL_MYSQL_READ_TIMEOUT", "35")),
    )
    llm = LlmSettings(
        base_url=os.getenv("TEXT2SQL_LLM_BASE_URL"),
        api_key=os.getenv("TEXT2SQL_LLM_API_KEY"),
        model=os.getenv("TEXT2SQL_LLM_MODEL", "gpt-4.1-mini"),
        timeout_seconds=int(os.getenv("TEXT2SQL_LLM_TIMEOUT", "8")),
        temperature=float(os.getenv("TEXT2SQL_LLM_TEMPERATURE", "0")),
        max_tokens=int(os.getenv("TEXT2SQL_LLM_MAX_TOKENS", "900")),
        transport=os.getenv("TEXT2SQL_LLM_TRANSPORT", "openai").strip().lower(),
        policy=os.getenv("TEXT2SQL_LLM_POLICY", "auto").strip().lower(),
    )
    mysql_mcp = MySqlMcpSettings(
        command=os.getenv("TEXT2SQL_MYSQL_MCP_COMMAND", "mcp-mysql-server"),
        args=shlex.split(os.getenv("TEXT2SQL_MYSQL_MCP_ARGS", "")),
        connection_name=os.getenv("TEXT2SQL_MYSQL_MCP_CONNECTION", mysql.database or "default"),
        database=os.getenv("TEXT2SQL_MYSQL_MCP_DATABASE", mysql.database or ""),
        timeout_seconds=int(os.getenv("TEXT2SQL_MYSQL_MCP_TIMEOUT", "35")),
    )
    intent_vector = IntentVectorSettings(
        enabled=_env_bool("TEXT2SQL_INTENT_VECTOR_ENABLED", False),
        provider=os.getenv("TEXT2SQL_INTENT_VECTOR_PROVIDER", "local").strip().lower(),
        distance_threshold=float(os.getenv("TEXT2SQL_INTENT_VECTOR_DISTANCE_THRESHOLD", "0.62")),
        timeout_ms=int(os.getenv("TEXT2SQL_INTENT_VECTOR_TIMEOUT_MS", "350")),
        top_k=int(os.getenv("TEXT2SQL_INTENT_VECTOR_TOP_K", "6")),
        base_url=os.getenv("TEXT2SQL_INTENT_EMBEDDING_BASE_URL"),
        api_key=os.getenv("TEXT2SQL_INTENT_EMBEDDING_API_KEY"),
        model=os.getenv("TEXT2SQL_INTENT_EMBEDDING_MODEL"),
        dimensions=int(os.getenv("TEXT2SQL_INTENT_EMBEDDING_DIMENSIONS", "0")),
        proxy_url=os.getenv("TEXT2SQL_INTENT_EMBEDDING_PROXY_URL"),
    )
    return RuntimeSettings(
        project_root=root,
        execution_mode=os.getenv("TEXT2SQL_EXECUTION_MODE", "dry_run"),
        executor_backend=os.getenv("TEXT2SQL_EXECUTOR_BACKEND", "direct_mysql").strip().lower(),
        allow_sensitive_fields=_env_bool("TEXT2SQL_ALLOW_SENSITIVE_FIELDS", False),
        audit_db_path=audit_path,
        mysql=mysql,
        mysql_mcp=mysql_mcp,
        llm=llm,
        intent_vector=intent_vector,
        performance=performance,
    )


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
