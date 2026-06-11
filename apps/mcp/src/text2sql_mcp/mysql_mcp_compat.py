from __future__ import annotations

import asyncio
import os
from typing import Any

import mysql_mcp_server.server as upstream
from mysql.connector import connect as mysql_connect


def connect(*args: Any, **kwargs: Any):
    if os.getenv("MYSQL_MCP_SSL_DISABLED", "true").strip().lower() in {"1", "true", "yes"}:
        kwargs.setdefault("ssl_disabled", True)
    return mysql_connect(*args, **kwargs)


def main() -> None:
    upstream.connect = connect
    asyncio.run(upstream.main())


if __name__ == "__main__":
    main()

