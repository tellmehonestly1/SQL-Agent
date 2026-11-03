"""Hybrid schema linking: lexical + vector RRF and one-hop FK expansion."""

import re
from collections import defaultdict
from functools import lru_cache
from rag.ingest import build_vector_store, load_catalog


def tokens(value):
    value = value.lower()
    words = set(re.findall(r"[a-z_][a-z_0-9]*", value))
    for part in re.findall(r"[\u4e00-\u9fff]+", value):
        words.update(part[i : i + 2] for i in range(max(1, len(part) - 1)))
    return words


def hybrid_rank(question, vector_docs, catalog, k=3):
    query = tokens(question)
    lexical = [
        (len(query & tokens(d.page_content + " " + d.metadata.get("title", ""))), d)
        for d in catalog
    ]
    lexical = sorted(
        (x for x in lexical if x[0]), key=lambda x: (-x[0], x[1].metadata["doc_id"])
    )[:20]
    scores, by_id = defaultdict(float), {}
    for ranked in (vector_docs, [d for _, d in lexical]):
        seen = set()
        for rank, doc in enumerate(ranked, 1):
            doc_id = doc.metadata["doc_id"]
            if doc_id in seen:
                continue
            seen.add(doc_id)
            by_id[doc_id] = doc
            scores[doc_id] += 1 / (60 + rank)
    selected = [by_id[i] for i in sorted(scores, key=lambda i: (-scores[i], i))[:k]]
    related = {
        t
        for d in selected
        for t in d.metadata.get("tables", []) + d.metadata.get("related_tables", [])
    }
    existing = {d.metadata["doc_id"] for d in selected}
    for d in catalog:
        if d.metadata["doc_id"].startswith("schema:") and related.intersection(
            d.metadata.get("tables", [])
        ):
            if d.metadata["doc_id"] not in existing:
                selected.append(d)
                existing.add(d.metadata["doc_id"])
    return selected


@lru_cache(maxsize=1)
def cached_catalog():
    return load_catalog()


def retrieve_context(
    question: str, k: int = 3, mode: str = "hybrid", max_chars: int = 12000
) -> str:
    if mode == "none":
        return ""
    vector_docs = build_vector_store().similarity_search(
        question, k=max(k, 10) if mode == "hybrid" else k
    )
    docs = (
        hybrid_rank(question, vector_docs, cached_catalog(), k)
        if mode == "hybrid"
        else vector_docs
    )
    parts, size = [], 0
    for d in docs:
        block = f"[{d.metadata.get('title', '')}] {d.page_content}"
        if size + len(block) + 2 > max_chars:
            break
        parts.append(block)
        size += len(block) + 2
    return "\n\n".join(parts)
