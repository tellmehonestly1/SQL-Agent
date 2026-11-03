import asyncio
import pytest
from conversations import ConversationStore, SessionError


def test_restart_owner_delete_and_bounded_snapshots(tmp_path):
    path = tmp_path / "sessions.sqlite"

    async def run():
        async with ConversationStore.open(path, max_turns=2) as store:
            sid = (await store.create("alice"))["session_id"]
            for i in range(5):
                previous = await store.checkout(sid, "alice")
                previous["history"] = previous.get("history", []) + [
                    {"question": str(i), "answer": "ok"}
                ]
                await store.commit(sid, "alice", previous)
                await store.release(sid)
            async with store.conn.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            ) as cur:
                assert (await cur.fetchone())[0] == 1
        async with ConversationStore.open(path) as store:
            with pytest.raises(SessionError, match="session_not_found"):
                await store.checkout(sid, "bob")
            previous = await store.checkout(sid, "alice")
            assert [t["question"] for t in previous["history"]] == ["3", "4"]
            with pytest.raises(SessionError, match="session_busy"):
                await store.checkout(sid, "alice")
            await store.release(sid)
            await store.delete(sid, "alice")
            with pytest.raises(SessionError):
                await store.checkout(sid, "alice")
            async with store.conn.execute("SELECT COUNT(*) FROM checkpoints") as cur:
                assert (await cur.fetchone())[0] == 0

    asyncio.run(run())


def test_expiration_and_capacity(tmp_path):
    async def run():
        now = [100.0]
        async with ConversationStore.open(
            tmp_path / "sessions.sqlite", ttl=10, max_sessions=1, clock=lambda: now[0]
        ) as store:
            sid = (await store.create("alice"))["session_id"]
            with pytest.raises(SessionError, match="sessions_capacity"):
                await store.create("alice")
            now[0] = 111
            await store.create("bob")
            with pytest.raises(SessionError):
                await store.checkout(sid, "alice")

    asyncio.run(run())


def test_single_worker_lock(tmp_path):
    async def run():
        path = tmp_path / "sessions.sqlite"
        async with ConversationStore.open(path):
            with pytest.raises(RuntimeError, match="one API worker"):
                async with ConversationStore.open(path):
                    pass

    asyncio.run(run())


def test_failed_snapshot_publish_keeps_previous_commit(tmp_path, monkeypatch):
    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            sid = (await store.create("alice"))["session_id"]
            await store.checkout(sid, "alice")
            await store.commit(
                sid, "alice", {"history": [{"question": "old", "answer": "good"}]}
            )
            original = store.graph.ainvoke

            async def broken(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("publish failed")

            monkeypatch.setattr(store.graph, "ainvoke", broken)
            with pytest.raises(RuntimeError):
                await store.commit(
                    sid, "alice", {"history": [{"question": "bad", "answer": "bad"}]}
                )
            await store.release(sid)
            previous = await store.checkout(sid, "alice")
            assert previous["history"][0]["question"] == "old"
            await store.release(sid)
            async with store.conn.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            ) as cur:
                assert (await cur.fetchone())[0] == 1

    asyncio.run(run())


def test_cancelled_publish_finishes_complete_snapshot(tmp_path, monkeypatch):
    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            sid = (await store.create("alice"))["session_id"]
            await store.checkout(sid, "alice")
            started = asyncio.Event()
            allow = asyncio.Event()
            original = store._commit

            async def delayed(*args, **kwargs):
                started.set()
                await allow.wait()
                await original(*args, **kwargs)

            monkeypatch.setattr(store, "_commit", delayed)
            task = asyncio.create_task(
                store.commit(
                    sid,
                    "alice",
                    {"history": [{"question": "complete", "answer": "good"}]},
                )
            )
            await started.wait()
            task.cancel()
            allow.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await store.release(sid)
            previous = await store.checkout(sid, "alice")
            assert previous["history"][0]["question"] == "complete"
            await store.release(sid)

    asyncio.run(run())


def test_cancelled_checkout_releases_lease(tmp_path, monkeypatch):
    async def run():
        async with ConversationStore.open(tmp_path / "sessions.sqlite") as store:
            sid = (await store.create("alice"))["session_id"]
            await store.checkout(sid, "alice")
            await store.commit(sid, "alice", {"history": []})
            await store.release(sid)
            original = store.graph.aget_state
            started = asyncio.Event()
            allow = asyncio.Event()

            async def delayed(*args, **kwargs):
                started.set()
                await allow.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(store.graph, "aget_state", delayed)
            task = asyncio.create_task(store.checkout(sid, "alice"))
            await started.wait()
            task.cancel()
            allow.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            monkeypatch.setattr(store.graph, "aget_state", original)
            await store.checkout(sid, "alice")
            await store.release(sid)

    asyncio.run(run())
