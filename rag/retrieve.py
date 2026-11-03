"""检索：根据用户问题，从 pgvector 里取最相关的 schema 文档。"""
from rag.ingest import build_vector_store


def retrieve_context(question: str, k: int = 3) -> str:
    """返回 top-k 条相关文档拼成的文本，供 graph 的 retrieve 节点用。"""
    store = build_vector_store()
    docs = store.similarity_search(question, k=k)
    if not docs:
        return ""
    return "\n\n".join(
        f"[{d.metadata.get('title', '')}] {d.page_content}" for d in docs
    )
