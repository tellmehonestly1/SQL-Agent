"""Bounded SQLite/LangGraph conversation snapshots with owner checks and leases.

The SQL execution graph is deliberately stateless. Only completed answers or
clarification state are committed, so a failed tool loop cannot poison a session.
"""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from typing import TypedDict
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, START, END


class SessionError(RuntimeError):
    def __init__(self, code, status=404):
        super().__init__(code)
        self.status = status


class ConversationState(TypedDict, total=False):
    history: list[dict]
    slots: dict
    pending: dict | None


class ProcessLock:
    def __init__(self, path):
        self.handle = open(path, "a+b")
        try:
            self.handle.seek(0)
            if self.handle.read(1) == b"":
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("Session SQLite store supports one API worker per file")

    def close(self):
        self.handle.close()


class ConversationStore:
    @classmethod
    @asynccontextmanager
    async def open(
        cls, path, ttl=1800, max_sessions=1000, max_turns=8, clock=time.time
    ):
        if min(ttl, max_sessions, max_turns) <= 0:
            raise ValueError("Session limits must be positive")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        process = ProcessLock(str(path) + ".lock")
        try:
            async with AsyncExitStack() as stack:
                saver = await stack.enter_async_context(
                    AsyncSqliteSaver.from_conn_string(str(path))
                )
                await saver.aget_tuple({"configurable": {"thread_id": "bootstrap"}})
                conn = await stack.enter_async_context(aiosqlite.connect(str(path)))
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS analysis_sessions (session_id TEXT PRIMARY KEY, "
                    "owner TEXT NOT NULL, thread_id TEXT, expires_at REAL NOT NULL, busy INTEGER NOT NULL DEFAULT 0)"
                )
                await conn.execute("UPDATE analysis_sessions SET busy=0")
                await conn.commit()
                self = cls()
                self.conn, self.saver = conn, saver
                self.ttl, self.max_sessions, self.max_turns = (
                    ttl,
                    max_sessions,
                    max_turns,
                )
                self.clock, self.gate = clock, asyncio.Lock()
                graph = StateGraph(ConversationState)
                graph.add_node("commit", lambda state: {})
                graph.add_edge(START, "commit")
                graph.add_edge("commit", END)
                self.graph = graph.compile(checkpointer=saver)
                # Recover unreferenced staging snapshots after an interrupted process.
                async with conn.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id NOT IN "
                    "(SELECT thread_id FROM analysis_sessions WHERE thread_id IS NOT NULL)"
                ) as cursor:
                    orphans = await cursor.fetchall()
                for (thread_id,) in orphans:
                    await saver.adelete_thread(thread_id)
                yield self
        finally:
            process.close()

    async def _purge(self):
        async with self.conn.execute(
            "SELECT thread_id FROM analysis_sessions WHERE expires_at<=? AND busy=0",
            (self.clock(),),
        ) as cur:
            old = await cur.fetchall()
        await self.conn.execute(
            "DELETE FROM analysis_sessions WHERE expires_at<=? AND busy=0",
            (self.clock(),),
        )
        await self.conn.commit()
        for (thread_id,) in old:
            if thread_id:
                await self.saver.adelete_thread(thread_id)

    async def create(self, owner):
        async with self.gate:
            await self._purge()
            async with self.conn.execute(
                "SELECT COUNT(*) FROM analysis_sessions"
            ) as cur:
                count = (await cur.fetchone())[0]
            if count >= self.max_sessions:
                raise SessionError("sessions_capacity", 429)
            session_id = str(uuid.uuid4())
            expires = self.clock() + self.ttl
            await self.conn.execute(
                "INSERT INTO analysis_sessions(session_id,owner,expires_at) VALUES (?,?,?)",
                (session_id, owner, expires),
            )
            await self.conn.commit()
            return {
                "session_id": session_id,
                "expires_at": expires,
                "ttl_seconds": self.ttl,
            }

    async def checkout(self, session_id, owner):
        task = asyncio.create_task(self._checkout(session_id, owner))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException:
                pass
            else:
                await self.release(session_id)
            raise

    async def _checkout(self, session_id, owner):
        async with self.gate:
            await self._purge()
            async with self.conn.execute(
                "SELECT thread_id,busy FROM analysis_sessions WHERE session_id=? AND owner=? AND expires_at>?",
                (session_id, owner, self.clock()),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise SessionError("session_not_found")
            if row[1]:
                raise SessionError("session_busy", 409)
            await self.conn.execute(
                "UPDATE analysis_sessions SET busy=1 WHERE session_id=?", (session_id,)
            )
            await self.conn.commit()
            try:
                snapshot = (
                    await self.graph.aget_state({"configurable": {"thread_id": row[0]}})
                    if row[0]
                    else None
                )
                return (
                    dict(snapshot.values)
                    if snapshot
                    else {"history": [], "slots": {}, "pending": None}
                )
            except BaseException:
                await self.conn.execute(
                    "UPDATE analysis_sessions SET busy=0 WHERE session_id=?",
                    (session_id,),
                )
                await self.conn.commit()
                raise

    async def release(self, session_id):
        async with self.gate:
            await self.conn.execute(
                "UPDATE analysis_sessions SET busy=0 WHERE session_id=?", (session_id,)
            )
            await self.conn.commit()

    async def _commit(self, session_id, owner, state):
        state = {**state, "history": state.get("history", [])[-self.max_turns :]}
        staging = uuid.uuid4().hex
        published = False
        try:
            await self.graph.ainvoke(
                state, config={"configurable": {"thread_id": staging}}
            )
            async with self.gate:
                async with self.conn.execute(
                    "SELECT thread_id FROM analysis_sessions WHERE session_id=? AND owner=? AND busy=1",
                    (session_id, owner),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    raise SessionError("session_not_found")
                await self.conn.execute(
                    "UPDATE analysis_sessions SET thread_id=?,expires_at=? WHERE session_id=?",
                    (staging, self.clock() + self.ttl, session_id),
                )
                await self.conn.commit()
                published = True
                if row[0]:
                    await self.saver.adelete_thread(row[0])
        finally:
            if not published:
                await self.saver.adelete_thread(staging)

    async def commit(self, session_id, owner, state):
        # Complete the small publish transaction even if transport cancellation lands
        # here. The saved analysis is complete; never leave a dangling pointer.
        task = asyncio.create_task(self._commit(session_id, owner, state))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def delete(self, session_id, owner):
        async with self.gate:
            async with self.conn.execute(
                "SELECT thread_id,busy FROM analysis_sessions WHERE session_id=? AND owner=?",
                (session_id, owner),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                raise SessionError("session_not_found")
            if row[1]:
                raise SessionError("session_busy", 409)
            await self.conn.execute(
                "DELETE FROM analysis_sessions WHERE session_id=?", (session_id,)
            )
            await self.conn.commit()
            if row[0]:
                await self.saver.adelete_thread(row[0])
