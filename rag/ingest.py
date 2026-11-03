"""把 schema 文档向量化，写进 pgvector（一次性的"建索引"步骤）。

真实工程里这一步通常是个独立的离线任务 / 定时任务，表结构或文档变了就重跑。
"""
import os

from langchain_core.documents import Document
from langchain_postgres import PGVector

from db import get_schema
from rag.embed import build_embeddings
from rag.schema_docs import build_schema_documents

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5433/sqlagent"
COLLECTION = "schema_docs"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def _sqlalchemy_dsn() -> str:
    """给 SQLAlchemy（PGVector 内部用）的 URL。

    SQLAlchemy 的 `postgresql://` 默认走 psycopg2 驱动，而本项目只装 psycopg(v3)，
    所以必须写成 `postgresql+psycopg://` 才会用 psycopg3，否则报 No module named 'psycopg2'。
    而 psycopg.connect() 只认纯 `postgresql://`，所以这两种 URL 分开提供。
    """
    return _dsn().replace("postgresql://", "postgresql+psycopg://", 1)


def _ensure_vector_extension() -> None:
    """确保 pgvector 扩展已装。

    为什么这里再补一次：seed.sql 只在容器【首次】初始化时执行；如果 pgdata
    volume 已经存在，seed.sql 不会重跑，扩展就不会自动建。这里幂等地补一次。
    """
    import psycopg

    conn = psycopg.connect(_dsn(), connect_timeout=5)
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
    finally:
        conn.close()


def build_vector_store() -> PGVector:
    """拿到一个指向 pgvector 的向量库对象（只是建立连接，不重复向量化）。"""
    return PGVector(
        embeddings=build_embeddings(),
        collection_name=COLLECTION,
        connection=_sqlalchemy_dsn(),
        use_jsonb=True,
    )


def ingest() -> None:
    """建索引：schema 文档 → 向量 → pgvector。"""
    _ensure_vector_extension()
    docs = build_schema_documents(get_schema())
    documents = [
        Document(
            page_content=d["content"],
            metadata={"doc_id": d["id"], "title": d["title"]},
        )
        for d in docs
    ]
    store = build_vector_store()
    store.add_documents(documents)
    print(f"已写入 {len(documents)} 条 schema 文档到 pgvector（collection={COLLECTION}）")


if __name__ == "__main__":
    ingest()
