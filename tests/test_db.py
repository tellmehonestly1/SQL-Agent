import asyncio
import json
from types import SimpleNamespace
from contextlib import contextmanager
import db


def test_policy_precedes_connection(monkeypatch):
    def forbidden():
        raise AssertionError("DB must not be touched")

    monkeypatch.setattr(db, "get_connection", forbidden)
    assert (
        json.loads(db.run_sql("DELETE FROM users"))["error"]["code"] == "policy_denied"
    )


def test_server_cursor_and_row_limit(monkeypatch):
    class Cursor:
        description = [SimpleNamespace(name="id")]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql):
            pass

        def __iter__(self):
            return iter([(1,), (2,), (3,)])

    class Connection:
        def cursor(self, **kwargs):
            assert kwargs["name"] == "sqlagent_result"
            return Cursor()

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(db, "get_connection", connection)
    monkeypatch.setattr(db, "MAX_ROWS", 2)
    result = db.execute_query("SELECT id FROM users")
    assert result["rows"] == [[1], [2]] and result["truncated"]


def test_byte_limit_accounts_for_multibyte_and_metadata(monkeypatch):
    monkeypatch.setattr(db, "MAX_RESULT_BYTES", 100)
    assert not db._fits(["name"], [["汉" * 50]])
    assert db._fits(["name"], [["a"]])


def test_errors_do_not_leak_secrets():
    assert "password" not in json.dumps(db._error(RuntimeError("password=secret")))
    assert not db._error(RuntimeError())["error"]["retryable"]


def test_async_cancellation_is_not_reported_as_success(monkeypatch):
    class Context:
        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(
        db, "_async_pool", SimpleNamespace(connection=lambda: Context())
    )

    async def run():
        try:
            await db.arun_sql("SELECT id FROM users")
        except asyncio.CancelledError:
            return
        raise AssertionError("Cancellation swallowed")

    asyncio.run(run())
