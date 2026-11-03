import json
import pytest
from langchain_core.messages import AIMessage
from graph import nodes
from graph.graph import build_graph
from api import initial


def call(id="a"):
    return {"id": id, "name": "run_sql", "args": {"sql": "SELECT COUNT(*) FROM users"}}


def test_graph_loop_works_sync_and_tracks_budget(monkeypatch):
    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if len(messages) == 2:
                return AIMessage(
                    content="",
                    tool_calls=[call()],
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                    },
                )
            return AIMessage(content="3")

    monkeypatch.setattr(nodes, "retrieve_context", lambda *a, **k: "")
    monkeypatch.setattr(
        nodes,
        "run_sql",
        lambda sql: json.dumps(
            {"ok": True, "rows": [[3]], "columns": ["n"], "truncated": False}
        ),
    )
    graph = build_graph(model=Model())
    one = graph.invoke(initial("count"))
    two = graph.invoke(initial("count"))
    assert one["tool_count"] == two["tool_count"] == 1
    assert one["tokens"] == 12
    assert [e["node"] for e in one["trace"]] == ["retrieve", "agent", "tools", "agent"]


def test_tool_budget_checked_before_execution(monkeypatch):
    monkeypatch.setattr(nodes, "MAX_TOOL_CALLS", 1)
    state = {"messages": [AIMessage(content="", tool_calls=[call(), call("b")])]}
    with pytest.raises(nodes.AgentExecutionError, match="tool_budget"):
        nodes.tools_node(state)


def test_nonretryable_and_repair_limits(monkeypatch):
    state = {
        "messages": [AIMessage(content="", tool_calls=[call()])],
        "repair_count": 2,
    }
    monkeypatch.setattr(
        nodes,
        "run_sql",
        lambda sql: json.dumps(
            {"ok": False, "error": {"code": "invalid_query", "retryable": True}}
        ),
    )
    with pytest.raises(nodes.AgentExecutionError, match="repair_budget"):
        nodes.tools_node(state)
    monkeypatch.setattr(
        nodes,
        "run_sql",
        lambda sql: json.dumps(
            {"ok": False, "error": {"code": "policy_denied", "retryable": False}}
        ),
    )
    with pytest.raises(nodes.AgentExecutionError, match="policy_denied"):
        nodes.tools_node(state)


def test_token_budget():
    with pytest.raises(nodes.AgentExecutionError, match="token_budget"):
        nodes._before_agent({"tokens": nodes.MAX_TOKENS})


def test_final_answer_requires_complete_database_evidence():
    from langchain_core.messages import ToolMessage

    state = {"messages": []}
    with pytest.raises(nodes.AgentExecutionError, match="missing_query_evidence"):
        nodes._after_agent(state, AIMessage(content="invented"), 0)
    state["messages"] = [
        ToolMessage(
            content=json.dumps({"ok": True, "truncated": True}), tool_call_id="a"
        )
    ]
    with pytest.raises(nodes.AgentExecutionError, match="incomplete_query_evidence"):
        nodes._after_agent(state, AIMessage(content="partial"), 0)
