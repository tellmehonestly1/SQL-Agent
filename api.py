"""Bounded API with request deadlines, shared resources and deterministic cleanup."""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from pydantic import ValidationError
from db import open_async_pool, close_async_pool, close_pool
from graph.graph import build_graph
from graph.nodes import AgentExecutionError, RetrievalWorker
from rag.ingest import close_vector_store
from rag.retrieve import cached_catalog
from schemas import QueryRequest
from request_state import initial as initial
from conversations import ConversationStore, SessionError
from analysis_service import AnalysisService

logger = logging.getLogger("uvicorn.error.sqlagent")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "90"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_REQUESTS", "8"))


@asynccontextmanager
async def lifespan(app):
    worker = RetrievalWorker()
    try:
        await open_async_pool()
        app.state.graph = build_graph(worker)
        app.state.slots = asyncio.Queue(maxsize=MAX_CONCURRENT)
        for _ in range(MAX_CONCURRENT):
            app.state.slots.put_nowait(True)
        app.state.client_keys = json.loads(os.getenv("API_CLIENT_KEYS", "") or "{}")
        if not isinstance(app.state.client_keys, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in app.state.client_keys.items()
        ):
            raise ValueError(
                "API_CLIENT_KEYS must map nonempty API keys to owner identifiers"
            )
        async with ConversationStore.open(
            os.getenv("SESSION_DATABASE_PATH", ".data/sessions.sqlite"),
            ttl=int(os.getenv("SESSION_TTL_SECONDS", "1800")),
            max_sessions=int(os.getenv("MAX_SESSIONS", "1000")),
            max_turns=int(os.getenv("MAX_HISTORY_TURNS", "8")),
        ) as conversations:
            app.state.conversations = conversations
            app.state.analysis = AnalysisService(app.state.graph, conversations)
            yield
    finally:
        await close_async_pool()
        await asyncio.to_thread(worker.close)
        close_pool()
        close_vector_store()
        cached_catalog.cache_clear()


app = FastAPI(title="SQL Agent V2.2", version="2.2.0", lifespan=lifespan)


def principal(headers):
    keys = app.state.client_keys
    if not keys:
        return "local"
    owner = keys.get(headers.get("x-api-key", ""))
    if owner is None:
        raise HTTPException(status_code=401, detail="authentication_required")
    return owner


@app.post("/sessions", status_code=201)
async def create_session(request: Request):
    try:
        return await app.state.conversations.create(principal(request.headers))
    except SessionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@app.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: uuid.UUID, request: Request):
    try:
        await app.state.conversations.delete(
            str(session_id), principal(request.headers)
        )
    except SessionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


def acquire():
    try:
        app.state.slots.get_nowait()
    except asyncio.QueueEmpty:
        raise HTTPException(status_code=429, detail="service_busy")


def _summarize(chunk):
    node, update = next(iter(chunk.items()))
    output = {"node": node, "trace": update.get("trace", [])}
    if node == "retrieve":
        output["detail"] = update.get("context", "")[:200]
    elif node == "agent":
        message = update["messages"][-1]
        output.update(
            detail="" if message.tool_calls else message.content,
            tool_calls=[tc["name"] for tc in message.tool_calls],
        )
    else:
        output["detail"] = str(update.get("tool_count", 0)) + " tool calls"
    return output


def _log(request_id, started, outcome, trace=None):
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "outcome": outcome,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "trace": trace or [],
            }
        )
    )


@app.post("/query")
async def query(req: QueryRequest, request: Request):
    owner = principal(request.headers)
    acquire()
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    outcome = "error"
    result = {}
    try:
        result = await asyncio.wait_for(
            app.state.analysis.run(req, owner),
            timeout=REQUEST_TIMEOUT,
        )
        outcome = "done"
        return {**result, "request_id": request_id}
    except asyncio.TimeoutError:
        outcome = "timeout"
        raise HTTPException(status_code=504, detail="request_timeout")
    except SessionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    except AgentExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="agent_unavailable")
    finally:
        app.state.slots.put_nowait(True)
        _log(request_id, started, outcome, result.get("trace"))


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    tasks = []
    acquired = False
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    outcome = "error"
    trace = []
    try:
        owner = principal(websocket.headers)
        req = QueryRequest.model_validate(
            await asyncio.wait_for(websocket.receive_json(), timeout=10)
        )
        try:
            acquire()
        except HTTPException:
            await websocket.send_json({"node": "error", "code": "service_busy"})
            return
        acquired = True
        queue = asyncio.Queue(maxsize=16)

        async def produce():
            async def on_update(chunk):
                item = _summarize(chunk)
                trace.extend(item["trace"])
                await queue.put(item)

            result = await app.state.analysis.run(req, owner, on_update=on_update)
            await queue.put(
                {
                    "node": "clarification"
                    if result["status"] == "clarification_required"
                    else "result",
                    **result,
                }
            )
            await queue.put({"node": "done"})

        async def forward():
            while True:
                item = await queue.get()
                await websocket.send_json({**item, "request_id": request_id})
                if item["node"] == "done":
                    return "done"

        async def watch():
            while True:
                message = await websocket.receive_json()
                if message.get("action") == "cancel":
                    return "cancelled"

        producer = asyncio.create_task(produce())
        sender = asyncio.create_task(forward())
        watcher = asyncio.create_task(watch())
        tasks = [producer, sender, watcher]

        async def supervise():
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                # A client cancellation/disconnect takes precedence over normal completion.
                if watcher in done:
                    return watcher.result()
                for task in done:
                    value = task.result()  # Surface producer failures immediately.
                    if task is sender:
                        return value
            return "done"

        outcome = await asyncio.wait_for(supervise(), timeout=REQUEST_TIMEOUT)
        if outcome == "cancelled":
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await websocket.send_json({"node": "cancelled", "request_id": request_id})
    except (ValidationError, ValueError, KeyError, AttributeError):
        await websocket.send_json({"node": "error", "code": "invalid_request"})
    except asyncio.TimeoutError:
        outcome = "timeout"
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.send_json({"node": "error", "code": "request_timeout"}),
                timeout=1,
            )
    except WebSocketDisconnect:
        outcome = "disconnected"
    except (SessionError, HTTPException) as exc:
        code = str(exc) if isinstance(exc, SessionError) else str(exc.detail)
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.send_json({"node": "error", "code": code}), timeout=1
            )
    except AgentExecutionError as exc:
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.send_json({"node": "error", "code": str(exc)}), timeout=1
            )
    except Exception:
        with suppress(Exception):
            await asyncio.wait_for(
                websocket.send_json({"node": "error", "code": "agent_unavailable"}),
                timeout=1,
            )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if acquired:
            app.state.slots.put_nowait(True)
        _log(request_id, started, outcome, trace)
        with suppress(Exception):
            await asyncio.wait_for(websocket.close(), timeout=1)
