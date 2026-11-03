import pytest
from sql_policy import SQLPolicyError, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT public.lower(name) FROM users",
        "SELECT CAST(name AS public.custom_type) FROM users",
        "DELETE FROM users",
        "SELECT 1; SELECT 2",
        "SELECT * INTO stolen FROM users",
        "WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x",
        "SELECT * FROM pg_catalog.pg_authid",
        "SELECT * FROM secret.users",
        "SELECT secret FROM users",
        "SELECT pg_sleep(1)",
        "SELECT set_config('transaction_read_only','off',false)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM users FOR UPDATE",
        "SELECT * FROM generate_series(1,100)",
        "COPY users TO STDOUT",
        "SET default_transaction_read_only=off",
        "WITH users AS (SELECT * FROM pg_catalog.pg_authid) SELECT * FROM users",
        "SELECT id FROM users u JOIN orders o ON u.id=o.user_id",
        "SELECT * FROM langchain_pg_embedding",
    ],
)
def test_denied(sql):
    with pytest.raises(SQLPolicyError):
        validate_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM users",
        "SELECT 'DELETE DROP UPDATE' AS text FROM users",
        "SELECT created_at FROM users",
        "SELECT * FROM public.users",
        "WITH counts AS (SELECT user_id,COUNT(*) n FROM orders GROUP BY user_id) SELECT user_id,n FROM counts",
        "SELECT u.id,SUM(o.amount) FROM users u JOIN orders o ON u.id=o.user_id GROUP BY u.id",
        "SELECT id FROM users UNION SELECT user_id FROM orders",
        "SELECT id FROM users WHERE id IN (SELECT user_id FROM orders)",
    ],
)
def test_allowed(sql):
    assert validate_sql(sql)


def test_unknown_column_in_star_is_not_exposed():
    query = validate_sql("SELECT * FROM users")
    assert '"id"' in query and "*" not in query
