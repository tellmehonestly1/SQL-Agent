"""Idempotent, incremental schema indexing with a persistent document catalog."""

import hashlib
import json
import os
from functools import lru_cache
from langchain_core.documents import Document
from langchain_postgres import PGVector
from sqlalchemy import create_engine, text
from db import get_schema
from rag.embed import build_embeddings
from rag.schema_docs import build_schema_documents

COLLECTION = "schema_docs_v21"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5433/sqlagent"


def _dsn():
    return os.getenv("INDEX_DATABASE_URL", os.getenv("DATABASE_URL", DEFAULT_DSN))


def _sqlalchemy_dsn():
    return _dsn().replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache(maxsize=1)
def catalog_engine():
    return create_engine(
        _sqlalchemy_dsn(),
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )


@lru_cache(maxsize=1)
def build_vector_store():
    return PGVector(
        embeddings=build_embeddings(),
        collection_name=COLLECTION,
        connection=catalog_engine(),
        use_jsonb=True,
        create_extension=False,
    )


def close_vector_store():
    build_vector_store.cache_clear()
    if catalog_engine.cache_info().currsize:
        catalog_engine().dispose()
        catalog_engine.cache_clear()


def load_catalog():
    with catalog_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT doc_id, content, metadata FROM sqlagent_schema_catalog_v21 ORDER BY doc_id"
            )
        )
        return [
            Document(
                page_content=r.content, metadata={**r.metadata, "doc_id": r.doc_id}
            )
            for r in rows
        ]


def ingest():
    # Serialize writers; readers continue using their own short-lived connections.
    with catalog_engine().connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(219381021)"))
        try:
            _ingest()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(219381021)"))


def _ingest():
    engine = catalog_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS sqlagent_schema_catalog_v21 ("
                "doc_id TEXT PRIMARY KEY, content TEXT NOT NULL, metadata JSONB NOT NULL, digest TEXT NOT NULL)"
            )
        )
    docs = build_schema_documents(get_schema())
    # Actual foreign keys supplement the manually supplied business semantics.
    with engine.connect() as conn:
        edges = conn.execute(
            text(
                "SELECT tc.table_name, ccu.table_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name=ccu.constraint_name "
                "AND tc.constraint_schema=ccu.constraint_schema "
                "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'"
            )
        ).fetchall()
        existing = dict(
            conn.execute(
                text("SELECT doc_id, digest FROM sqlagent_schema_catalog_v21")
            ).fetchall()
        )
    allowed = {t for d in docs for t in d["tables"]}
    store = build_vector_store()
    with engine.connect() as conn:
        actual_vector_ids = set(
            conn.execute(
                text(
                    "SELECT e.id FROM langchain_pg_embedding e JOIN langchain_pg_collection c "
                    "ON c.uuid=e.collection_id WHERE c.name=:collection"
                ),
                {"collection": COLLECTION},
            ).scalars()
        )

    changed = []
    current = set()
    for doc in docs:
        current.add(doc["id"])
        related = sorted(
            {
                b if a in doc["tables"] else a
                for a, b in edges
                if (a in doc["tables"] or b in doc["tables"])
                and a in allowed
                and b in allowed
            }
        )
        metadata = {
            "title": doc["title"],
            "tables": doc["tables"],
            "related_tables": related,
        }
        digest = hashlib.sha256(
            json.dumps(
                [doc["content"], metadata], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        if (
            existing.get(doc["id"]) != digest
            or f"{COLLECTION}:{doc['id']}" not in actual_vector_ids
        ):
            changed.append((doc, metadata, digest))
    if changed:
        store.add_documents(
            [
                Document(page_content=d["content"], metadata={**m, "doc_id": d["id"]})
                for d, m, _ in changed
            ],
            ids=[f"{COLLECTION}:{d['id']}" for d, _, _ in changed],
        )
    stale = sorted(set(existing) - current)
    stale_vectors = actual_vector_ids - {f"{COLLECTION}:{doc_id}" for doc_id in current}
    if stale_vectors:
        store.delete(ids=sorted(stale_vectors))
    with engine.begin() as conn:
        for d, m, digest in changed:
            conn.execute(
                text(
                    "INSERT INTO sqlagent_schema_catalog_v21(doc_id,content,metadata,digest) "
                    "VALUES (:id,:content,CAST(:metadata AS jsonb),:digest) ON CONFLICT(doc_id) DO UPDATE "
                    "SET content=EXCLUDED.content, metadata=EXCLUDED.metadata, digest=EXCLUDED.digest"
                ),
                {
                    "id": d["id"],
                    "content": d["content"],
                    "metadata": json.dumps(m, ensure_ascii=False),
                    "digest": digest,
                },
            )
        for doc_id in stale:
            conn.execute(
                text("DELETE FROM sqlagent_schema_catalog_v21 WHERE doc_id=:id"),
                {"id": doc_id},
            )
    print(
        f"indexed={len(changed)}, unchanged={len(docs) - len(changed)}, removed={len(stale)}"
    )


if __name__ == "__main__":
    try:
        ingest()
    finally:
        close_vector_store()
        from db import close_pool

        close_pool()
