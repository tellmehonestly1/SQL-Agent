"""Fail-closed PostgreSQL policy shared by all tool entry points."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

BUSINESS_SCHEMA = {
    "users": {"id": "INT", "name": "TEXT", "created_at": "TIMESTAMPTZ"},
    "orders": {
        "id": "INT",
        "user_id": "INT",
        "amount": "DECIMAL",
        "created_at": "TIMESTAMPTZ",
    },
}
SAFE_FUNCTIONS = {
    "AND",
    "OR",
    "CASE",
    "IF",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "COALESCE",
    "NULLIF",
    "ROUND",
    "ABS",
    "LOWER",
    "UPPER",
    "LENGTH",
    "TRIM",
    "CAST",
    "TRY_CAST",
    "EXTRACT",
    "DATE_TRUNC",
    "TIMESTAMP_TRUNC",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "ROW_NUMBER",
    "RANK",
    "DENSE_RANK",
    "LAG",
    "LEAD",
    "CONCAT",
    "SUBSTRING",
}


class SQLPolicyError(ValueError):
    pass


def validate_sql(sql: str, schema: dict | None = None) -> str:
    schema = schema or BUSINESS_SCHEMA
    if not sql.strip() or len(sql) > 8000:
        raise SQLPolicyError("SQL is empty or too long")
    try:
        statements = sqlglot.parse(sql, read="postgres")
        if len(statements) != 1 or not isinstance(
            statements[0], (exp.Select, exp.SetOperation)
        ):
            raise SQLPolicyError("Only one SELECT statement is allowed")
        tree = statements[0]
        forbidden = (exp.DDL, exp.DML, exp.Command, exp.Into, exp.Lock)
        if any(isinstance(node, forbidden) for node in tree.walk()):
            raise SQLPolicyError("Writes, SELECT INTO and row locks are forbidden")
        for data_type in tree.find_all(exp.DataType):
            if data_type.this == exp.DataType.Type.USERDEFINED:
                raise SQLPolicyError("Custom types are not allowed")
        for func in tree.find_all(exp.Func):
            if isinstance(func.parent, exp.Dot):
                raise SQLPolicyError("Qualified function calls are not allowed")
            name = (
                func.name.upper()
                if isinstance(func, exp.Anonymous)
                else func.sql_name().upper()
            )
            if name not in SAFE_FUNCTIONS:
                raise SQLPolicyError(f"Function is not allowed: {name}")
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if isinstance(source, exp.Table):
                    if (
                        source.catalog
                        or source.db not in ("", "public")
                        or source.name not in schema
                    ):
                        raise SQLPolicyError("Table is not authorized")
                    source.set("db", None)
        tree = qualify(
            tree,
            dialect="postgres",
            schema=schema,
            validate_qualify_columns=True,
            quote_identifiers=True,
        )
        return tree.sql(dialect="postgres")
    except SQLPolicyError:
        raise
    except Exception as exc:
        raise SQLPolicyError("Invalid SQL syntax or column reference") from exc
