import asyncio
import threading
from unittest.mock import AsyncMock
from langchain_core.messages import AIMessage
from fastapi.testclient import TestClient
import pytest
import api


class FakeGraph:
    def __init__(self):
        self.cancelled = threading.Event()
        self.fail = False
        self.hang = False

    async def ainvoke(self, state, config):
        if self.hang:
            await asyncio.Event().wait()
        return {
            "messages": [AIMessage(content="ok")],
            "context": "schema",
            "trace": [],
            "structured_answer": {"status": "completed", "answer": "ok"},
        }

    async def astream(self, state, **kwargs):
        try:
            yield {"retrieve": {"context": "schema", "trace": []}}
            if self.fail:
                raise RuntimeError("private password")
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DATABASE_PATH", str(tmp_path / "sessions.sqlite"))
    monkeypatch.delenv("API_CLIENT_KEYS", raising=False)
    graph = FakeGraph()
    monkeypatch.setattr(api, "build_graph", lambda worker: graph)
    monkeypatch.setattr(api, "open_async_pool", AsyncMock())
    monkeypatch.setattr(api, "close_async_pool", AsyncMock())
    with TestClient(api.app) as client:
        yield client, graph


def test_http_validation_and_reuse(client):
    client, graph = client
    assert client.post("/query", json={"question": " "}).status_code == 422
    assert client.post("/query", json={"question": "count"}).json()["answer"] == "ok"
    assert client.post("/query", json={"question": "count again"}).status_code == 200
    assert api.app.state.graph is graph


def test_http_overload(client):
    client, _ = client
    slots = api.app.state.slots
    for _ in range(api.MAX_CONCURRENT):
        slots.get_nowait()
    try:
        assert client.post("/query", json={"question": "count"}).status_code == 429
    finally:
        for _ in range(api.MAX_CONCURRENT):
            slots.put_nowait(True)


def test_http_deadline_releases_slot(client, monkeypatch):
    client, graph = client
    graph.hang = True
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 0.02)
    assert client.post("/query", json={"question": "count"}).status_code == 504
    assert api.app.state.slots.qsize() == api.MAX_CONCURRENT


def test_ws_cancel_cleans_up_before_ack(client):
    client, graph = client
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"question": "count"})
        assert ws.receive_json()["node"] == "retrieve"
        ws.send_json({"action": "cancel"})
        assert ws.receive_json()["node"] == "cancelled"
        assert graph.cancelled.is_set()


def test_ws_producer_error_terminates(client):
    client, graph = client
    graph.fail = True
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"question": "count"})
        messages = [ws.receive_json()]
        if messages[-1]["node"] != "error":
            messages.append(ws.receive_json())
        assert messages[-1] == {"node": "error", "code": "agent_unavailable"}
    assert graph.cancelled.is_set()


def test_ws_disconnect_cleans_up(client):
    client, graph = client
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"question": "count"})
        assert ws.receive_json()["node"] == "retrieve"
        ws.close()
        assert ws.receive()["type"] == "websocket.close"
    assert graph.cancelled.wait(2)


def test_clarification_and_authenticated_session_ownership(client):
    client, graph = client
    api.app.state.client_keys = {"alice-key": "alice", "bob-key": "bob"}
    alice = {"x-api-key": "alice-key"}
    bob = {"x-api-key": "bob-key"}
    assert client.post("/sessions").status_code == 401
    session = client.post("/sessions", headers=alice)
    assert session.status_code == 201
    sid = session.json()["session_id"]
    response = client.post(
        "/query", headers=alice, json={"question": "销售额是多少", "session_id": sid}
    )
    assert (
        response.status_code == 200
        and response.json()["status"] == "clarification_required"
    )
    denied = client.post(
        "/query",
        headers=bob,
        json={"question": "用户总数", "session_id": sid, "user_id": "alice"},
    )
    assert denied.status_code == 404
    assert client.delete(f"/sessions/{sid}", headers=bob).status_code == 404
    ready = client.post(
        "/query",
        headers=alice,
        json={
            "question": "按订单金额，全部时间",
            "metric_id": "order_amount",
            "period": "all_time",
            "session_id": sid,
        },
    )
    assert ready.status_code == 200 and ready.json()["status"] == "completed"
    assert client.delete(f"/sessions/{sid}", headers=alice).status_code == 204
    assert (
        client.post(
            "/query", headers=alice, json={"question": "用户总数", "session_id": sid}
        ).status_code
        == 404
    )


def test_ws_clarification_is_terminal_and_persisted(client):
    client, _ = client
    sid = client.post("/sessions").json()["session_id"]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"question": "销售额是多少", "session_id": sid})
        result = ws.receive_json()
        assert (
            result["node"] == "clarification"
            and result["status"] == "clarification_required"
        )
        assert ws.receive_json()["node"] == "done"
        assert ws.receive()["type"] == "websocket.close"
    answer = client.post(
        "/query", json={"question": "订单金额，全部时间", "session_id": sid}
    )
    assert answer.json()["status"] == "completed"


def test_ws_uses_same_authenticated_owner(client):
    client, _ = client
    api.app.state.client_keys = {"alice-key": "alice", "bob-key": "bob"}
    sid = client.post("/sessions", headers={"x-api-key": "alice-key"}).json()[
        "session_id"
    ]
    with client.websocket_connect("/ws", headers={"x-api-key": "bob-key"}) as ws:
        ws.send_json({"question": "用户总数", "session_id": sid})
        assert ws.receive_json() == {"node": "error", "code": "session_not_found"}
        assert ws.receive()["type"] == "websocket.close"
