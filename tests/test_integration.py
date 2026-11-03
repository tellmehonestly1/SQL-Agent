"""Opt-in isolated PostgreSQL tests. Never point TEST_ADMIN_DSN at business data."""

import asyncio
import json
import os
from pathlib import Path
import pytest

ADMIN = os.getenv("SQLAGENT_TEST_ADMIN_DSN")
READER = os.getenv("SQLAGENT_TEST_READER_DSN")
pytestmark = pytest.mark.skipif(
    not ADMIN or not READER, reason="isolated PostgreSQL DSNs not configured"
)


@pytest.fixture(scope="module", autouse=True)
def fixture_db():
    if not ADMIN or not READER:
        return
    import psycopg

    root = Path(__file__).parents[1]
    with psycopg.connect(ADMIN) as conn:
        seed = (root / "seed.sql").read_text(encoding="utf-8")
        seed = seed.replace(
            "CREATE TABLE users", "CREATE TABLE IF NOT EXISTS users"
        ).replace("CREATE TABLE orders", "CREATE TABLE IF NOT EXISTS orders")
        conn.execute(seed)
        conn.execute((root / "db_init/reader.sql").read_text(encoding="utf-8"))
        conn.execute((root / "evaluation/fixture.sql").read_text(encoding="utf-8"))


@pytest.fixture
def database(monkeypatch):
    import db

    monkeypatch.setenv("QUERY_DATABASE_URL", READER)
    db.close_pool()
    yield db
    db.close_pool()


def test_real_policy_execution_and_all_references(database):
    cases = json.loads(
        (Path(__file__).parents[1] / "evaluation/cases.json").read_text(
            encoding="utf-8"
        )
    )
    for case in cases:
        result = database.execute_query(case["reference_sql"])
        assert result["ok"], (case["id"], result)
        assert not result["truncated"]
    assert database.execute_raw("SELECT COUNT(*) FROM users") == [(6,)]
    assert database.execute_raw("SELECT SUM(amount) FROM orders")[0][0] == __import__(
        "decimal"
    ).Decimal("599.20")


def test_database_role_cannot_write_even_with_readonly_disabled():
    import psycopg

    with psycopg.connect(READER) as conn:
        conn.execute("SET default_transaction_read_only=off")
        conn.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM users")


def test_real_async_query_and_cancel_releases_connection(database):
    async def run():
        await database.open_async_pool()
        try:
            good = json.loads(await database.arun_sql("SELECT COUNT(*) FROM users"))
            assert good["rows"] == [[6]]
            # Heavy but read-only query: no permission to call pg_sleep is needed.
            sql = "SELECT COUNT(*) FROM " + " CROSS JOIN ".join(
                f"users u{i}" for i in range(12)
            )
            task = asyncio.create_task(database.arun_sql(sql))
            await asyncio.sleep(0.1)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            good = json.loads(await database.arun_sql("SELECT COUNT(*) FROM orders"))
            assert good["rows"] == [[8]]
            import psycopg

            with psycopg.connect(ADMIN) as conn:
                active = conn.execute(
                    "SELECT COUNT(*) FROM pg_stat_activity WHERE usename='sqlagent_reader' "
                    "AND state='active'"
                ).fetchone()[0]
                assert active == 0
        finally:
            await database.close_async_pool()

    asyncio.run(run())


def test_real_statement_timeout(database, monkeypatch):
    monkeypatch.setattr(database, "STATEMENT_TIMEOUT_MS", 30)
    sql = "SELECT COUNT(*) FROM " + " CROSS JOIN ".join(
        f"users u{i}" for i in range(12)
    )
    result = database.execute_query(sql)
    assert result["error"]["code"] == "query_timeout"


def test_real_vector_index_incremental_and_hybrid(database, monkeypatch):
    from langchain_core.embeddings import Embeddings
    import hashlib
    from rag import ingest, retrieve

    class FakeEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [self.embed_query(t) for t in texts]

        def embed_query(self, text):
            digest = hashlib.sha256(text.encode()).digest()
            return [(b - 127.5) / 127.5 for b in digest[:8]]

    monkeypatch.setenv("INDEX_DATABASE_URL", ADMIN)
    monkeypatch.setattr(ingest, "build_embeddings", lambda: FakeEmbeddings())
    ingest.close_vector_store()
    retrieve.cached_catalog.cache_clear()
    try:
        ingest.ingest()
        first = ingest.load_catalog()
        ingest.ingest()
        second = ingest.load_catalog()
        assert len(first) == len(second) == 6
        from sqlalchemy import text

        with ingest.catalog_engine().connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM langchain_pg_embedding e JOIN langchain_pg_collection c ON c.uuid=e.collection_id WHERE c.name='schema_docs_v21'"
                )
            ).scalar()
            assert count == 6
        # A missing vector is repaired even if its catalog hash is unchanged.
        ingest.build_vector_store().delete(ids=[f"{ingest.COLLECTION}:users"])
        ingest.ingest()
        result = retrieve.retrieve_context("订单金额和用户名")
        assert "orders" in result and "users" in result
        # Updating content overwrites the stable ID; removed source docs are deleted.
        original = ingest.build_schema_documents

        def changed(schema):
            docs = original(schema)
            docs[0]["content"] += " changed"
            return [d for d in docs if d["id"] != "order-count"]

        monkeypatch.setattr(ingest, "build_schema_documents", changed)
        ingest.ingest()
        assert len(ingest.load_catalog()) == 5
        with ingest.catalog_engine().connect() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM langchain_pg_embedding e JOIN langchain_pg_collection c ON c.uuid=e.collection_id WHERE c.name='schema_docs_v21'"
                    )
                ).scalar()
                == 5
            )
    finally:
        ingest.close_vector_store()
        retrieve.cached_catalog.cache_clear()


def test_real_mcp_stdio(database):
    import subprocess
    import sys

    environment = {**os.environ, "QUERY_DATABASE_URL": READER, "PYTHONUTF8": "1"}
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "mcp_client.py"],
        cwd=root,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "run_sql" in result.stdout
    assert '"rows": [[6]]' in result.stdout


def test_asia_shanghai_month_boundary(database):
    import psycopg
    from decimal import Decimal

    with psycopg.connect(ADMIN) as conn:
        inserted = conn.execute(
            "INSERT INTO orders(user_id,amount,created_at) VALUES (1,1,'2026-02-01T00:00:00+08:00') RETURNING id"
        ).fetchone()[0]
    try:
        total = database.execute_raw(
            "SELECT SUM(amount) FROM orders WHERE created_at>='2026-01-01' AND created_at<'2026-02-01'"
        )
        assert total == [(Decimal("160.40"),)]
    finally:
        with psycopg.connect(ADMIN) as conn:
            conn.execute("DELETE FROM orders WHERE id=%s", (inserted,))


def test_real_multi_turn_semantic_and_provenance_flow(database, tmp_path):
    from langchain_core.messages import AIMessage, ToolMessage
    from analysis_service import AnalysisService
    from conversations import ConversationStore
    from graph.graph import build_graph
    from graph.nodes import RetrievalWorker
    from schemas import QueryRequest
    from provenance import citations_valid
    from eval import rows_equal

    class ScriptedModel:
        """Tests orchestration against real SQL. This is NOT an accuracy benchmark."""

        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="虚构数字999999不应进入答案")
            self.calls += 1
            contract = json.loads(
                messages[0]
                .content.split("本轮已确认的业务口径（优先于历史条件）：", 1)[1]
                .split("。日期按", 1)[0]
            )
            metric = contract["metric_id"]
            table = "users" if metric == "user_count" else "orders"
            aggregate = {
                "order_amount": "SUM(amount)",
                "user_count": "COUNT(*)",
                "order_count": "COUNT(*)",
                "ordering_users": "COUNT(DISTINCT user_id)",
            }[metric]
            sql = f"SELECT {aggregate} FROM {table}"
            bounds = contract["date_range"]
            where = (
                (
                    f" WHERE created_at>='{bounds['start']}T00:00:00+08:00' AND created_at<'{bounds['end']}T00:00:00+08:00'"
                )
                if bounds
                else ""
            )
            if "按用户ID排名" in messages[-1].content:
                sql = (
                    "SELECT user_id,SUM(amount) total FROM orders"
                    + where
                    + " GROUP BY user_id ORDER BY total DESC,user_id"
                )
            else:
                sql += where
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": f"call{self.calls}", "name": "run_sql", "args": {"sql": sql}}
                ],
            )

    async def run():
        worker = RetrievalWorker()
        await database.open_async_pool()
        model = ScriptedModel()
        records = []
        try:
            graph = build_graph(worker, retrieval_mode="none", model=model)
            async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
                service = AnalysisService(graph, store)
                episodes = json.loads(
                    (
                        Path(__file__).parents[1] / "evaluation/conversations.json"
                    ).read_text(encoding="utf-8")
                )
                for episode in episodes:
                    sid = (await store.create("alice"))["session_id"]
                    for turn in episode["turns"]:
                        before = model.calls
                        result = await service.run(
                            QueryRequest(**turn["request"], session_id=sid), "alice"
                        )
                        assert result["status"] == turn["expected_status"]
                        if result["status"] == "completed":
                            assert model.calls == before + 1
                            assert citations_valid(result)
                            assert "999999" not in result["answer"]
                            assert rows_equal(
                                result["data"]["rows"],
                                database.execute_raw(turn["reference_sql"]),
                                turn.get("ordered", False),
                            )
                        else:
                            assert model.calls == before
                        records.append(
                            {
                                "episode": episode["id"],
                                "request": turn["request"],
                                "response": {
                                    k: v
                                    for k, v in result.items()
                                    if k not in ("session_id", "context")
                                },
                            }
                        )
                assert model.calls == 6
            if os.getenv("SQLAGENT_DEMO_OUTPUT"):
                Path(os.environ["SQLAGENT_DEMO_OUTPUT"]).write_text(
                    json.dumps(
                        {
                            "model": "deterministic test double; NOT a real model accuracy result",
                            "database": "isolated PostgreSQL fixture",
                            "turns": records,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        finally:
            await database.close_async_pool()
            await asyncio.to_thread(worker.close)

    asyncio.run(run())
