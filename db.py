"""Bounded structured queries with shared policy, pooling and async cancellation."""

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from functools import lru_cache
from sql_policy import BUSINESS_SCHEMA, SQLPolicyError, validate_sql

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

MAX_ROWS = 1000
MAX_RESULT_BYTES = 32000
STATEMENT_TIMEOUT_MS = 5000


def database_url() -> str:
    return os.getenv(
        "QUERY_DATABASE_URL",
        "postgresql://sqlagent_reader:sqlagent_reader@localhost:5433/sqlagent",
    )


@lru_cache(maxsize=1)
def get_pool():
    from psycopg_pool import ConnectionPool

    return ConnectionPool(
        database_url(),
        min_size=0,
        max_size=int(os.getenv("DB_POOL_SIZE", "8")),
        timeout=5,
        open=True,
        kwargs={
            "connect_timeout": 5,
            "options": f"-c timezone=Asia/Shanghai -c search_path=pg_catalog,public -c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}",
        },
    )


@contextmanager
def get_connection():
    with get_pool().connection() as conn:
        yield conn


def close_pool():
    if get_pool.cache_info().currsize:
        get_pool().close()
        get_pool.cache_clear()


def _error(exc: Exception) -> dict:
    code = getattr(exc, "sqlstate", "") or ""
    if isinstance(exc, SQLPolicyError) or code == "42501":
        kind, retryable = "policy_denied", False
    elif code == "57014":
        kind, retryable = "query_timeout", False
    elif code.startswith("42"):
        kind, retryable = "invalid_query", True
    else:
        kind, retryable = "database_unavailable", False
    return {"ok": False, "error": {"code": kind, "retryable": retryable}}


def _payload(columns, rows, truncated=False):
    return {"ok": True, "columns": columns, "rows": rows, "truncated": truncated}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _fits(columns, rows):
    return len(rows) <= MAX_ROWS and _size(_payload(columns, rows)) <= MAX_RESULT_BYTES


def _finish(columns, rows, truncated):
    result = _payload(columns, rows, truncated)
    if _size(result) > MAX_RESULT_BYTES:
        return {"ok": False, "error": {"code": "result_too_wide", "retryable": False}}
    return result


def execute_query(sql: str) -> dict:
    try:
        sql = validate_sql(sql)
        # Server-side cursor avoids buffering an unbounded result in the client.
        with get_connection() as conn, conn.cursor(name="sqlagent_result") as cur:
            cur.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows, truncated = [], False
            for row in cur:
                candidate = rows + [list(row)]
                if not _fits(columns, candidate):
                    truncated = True
                    break
                rows = candidate
            return _finish(columns, rows, truncated)
    except Exception as exc:
        return _error(exc)


def run_sql(sql: str) -> str:
    return json.dumps(execute_query(sql), ensure_ascii=False, default=str)


_async_pool = None


async def open_async_pool():
    global _async_pool
    from psycopg_pool import AsyncConnectionPool

    _async_pool = AsyncConnectionPool(
        database_url(),
        min_size=0,
        max_size=int(os.getenv("DB_POOL_SIZE", "8")),
        open=False,
        timeout=5,
        kwargs={
            "connect_timeout": 5,
            "options": f"-c timezone=Asia/Shanghai -c search_path=pg_catalog,public -c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}",
        },
    )
    await _async_pool.open()


async def close_async_pool():
    global _async_pool
    if _async_pool is not None:
        await _async_pool.close()
        _async_pool = None


async def arun_sql(sql: str) -> str:
    try:
        sql = validate_sql(sql)
        if _async_pool is None:
            raise RuntimeError("async pool not open")
        async with _async_pool.connection() as conn:
            async with conn.cursor(name="sqlagent_result") as cur:
                await cur.execute(sql)
                columns = [d.name for d in cur.description] if cur.description else []
                rows, truncated = [], False
                async for row in cur:
                    candidate = rows + [list(row)]
                    if not _fits(columns, candidate):
                        truncated = True
                        break
                    rows = candidate
                return json.dumps(
                    _finish(columns, rows, truncated), ensure_ascii=False, default=str
                )
    except asyncio.CancelledError:
        # Psycopg async execution handles server cancellation before pool reuse.
        raise
    except Exception as exc:
        return json.dumps(_error(exc), ensure_ascii=False)


def execute_raw(sql: str) -> list[tuple]:
    result = execute_query(sql)
    if not result["ok"] or result["truncated"]:
        raise ValueError("Evaluation query failed or was truncated")
    return [tuple(row) for row in result["rows"]]


def get_schema() -> str:
    """Only authorized metadata: column types/comments, primary keys and FK paths."""
    tables = {}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod), "
            "col_description(c.oid,a.attnum),obj_description(c.oid,'pg_class'), "
            "EXISTS(SELECT 1 FROM pg_index i WHERE i.indrelid=c.oid AND i.indisprimary AND a.attnum=ANY(i.indkey)) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY c.relname,a.attnum",
            (list(BUSINESS_SCHEMA),),
        )
        comments = {}
        for (
            table,
            column,
            dtype,
            column_comment,
            table_comment,
            primary,
        ) in cur.fetchall():
            if column not in BUSINESS_SCHEMA[table]:
                continue
            comment = (column_comment or "").replace("\n", " ")
            detail = f"{column} ({dtype})" + (" PRIMARY KEY" if primary else "")
            if comment:
                detail += f" COMMENT={comment}"
            tables.setdefault(table, []).append(detail)
            comments[table] = (table_comment or "").replace("\n", " ")
        cur.execute(
            "SELECT src.relname,pg_get_constraintdef(k.oid) FROM pg_constraint k "
            "JOIN pg_class src ON src.oid=k.conrelid JOIN pg_namespace n ON n.oid=src.relnamespace "
            "JOIN pg_class dst ON dst.oid=k.confrelid JOIN pg_namespace dn ON dn.oid=dst.relnamespace "
            "WHERE k.contype='f' AND n.nspname='public' AND dn.nspname='public' "
            "AND src.relname=ANY(%s) AND dst.relname=ANY(%s)",
            (list(BUSINESS_SCHEMA), list(BUSINESS_SCHEMA)),
        )
        foreign_keys = {}
        for table, definition in cur.fetchall():
            foreign_keys.setdefault(table, []).append(definition)
    lines = []
    for table, cols in tables.items():
        line = f"- {table}({', '.join(cols)})"
        if comments.get(table):
            line += f" | COMMENT={comments[table]}"
        if foreign_keys.get(table):
            line += " | " + "; ".join(foreign_keys[table])
        lines.append(line)
    return "\n".join(lines)
