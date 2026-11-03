from langchain_core.documents import Document
from rag.retrieve import hybrid_rank
from rag.schema_docs import build_schema_documents


def doc(id, content, tables, related=None):
    return Document(
        page_content=content,
        metadata={
            "doc_id": id,
            "title": id,
            "tables": tables,
            "related_tables": related or [],
        },
    )


def test_hybrid_finds_lexical_match_and_expands_join():
    orders = doc("schema:orders", "订单 金额 amount", ["orders"], ["users"])
    users = doc("schema:users", "用户 姓名", ["users"], ["orders"])
    noise = doc("noise", "unrelated", [])
    selected = hybrid_rank("订单金额", [noise, orders], [orders, users, noise], k=1)
    assert [d.metadata["doc_id"] for d in selected] == ["schema:orders", "schema:users"]


def test_duplicate_vector_hits_do_not_boost_rank():
    a = doc("a", "订单", [])
    b = doc("b", "用户", [])
    selected = hybrid_rank("用户", [a, a, a, b], [a, b], k=1)
    assert selected[0].metadata["doc_id"] == "b"


def test_schema_split_and_stable_ids():
    docs = build_schema_documents("- users(id (integer))\n- orders(id (integer))")
    structural = [d for d in docs if d["id"].startswith("schema:")]
    assert [d["id"] for d in structural] == ["schema:users", "schema:orders"]
