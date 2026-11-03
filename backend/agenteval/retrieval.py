"""Independent FTS5 retriever and TF-IDF cosine reranker (no model download)."""
import math
import re
import sqlite3
from collections import Counter


def tokens(text):
    return re.findall(r"\w+", text.lower())


def fts_retrieve(value):
    documents = value["documents"]
    query = tokens(value["query"])
    if not query:
        return []
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE corpus USING fts5(id UNINDEXED, text)")
        db.executemany("INSERT INTO corpus VALUES (?,?)", [(d["id"], d["text"]) for d in documents])
        # Quoted terms prevent FTS operators/injection from untrusted query text.
        expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in query)
        rows = db.execute("SELECT id, text, bm25(corpus) FROM corpus WHERE corpus MATCH ? ORDER BY bm25(corpus),id LIMIT ?",
                          (expression, value.get("k", 5))).fetchall()
    return [{"id": row[0], "text": row[1], "score": -row[2], "metadata": {"method": "sqlite_fts5_bm25"}} for row in rows]


def tfidf_rerank(value):
    documents = value["documents"]
    counts = [Counter(tokens(d["text"])) for d in documents]
    query = Counter(tokens(value["query"]))
    df = Counter(word for count in counts for word in count)
    idf = {word: math.log((1 + len(documents)) / (1 + df[word])) + 1 for word in set(df) | set(query)}
    def vector(count):
        return {word: (1 + math.log(freq)) * idf[word] for word, freq in count.items()}
    q = vector(query)
    qnorm = math.sqrt(sum(v * v for v in q.values()))
    ranked = []
    for doc, count in zip(documents, counts):
        v = vector(count)
        norm = math.sqrt(sum(x * x for x in v.values()))
        score = sum(weight * v.get(word, 0) for word, weight in q.items()) / (qnorm * norm) if qnorm and norm else 0.0
        ranked.append({**doc, "score": score, "metadata": {"method": "tfidf_cosine"}})
    return sorted(ranked, key=lambda d: (-d["score"], d["id"]))[:value.get("k", 5)]


def component_response(documents):
    ids = [d["id"] for d in documents]
    if len(ids) != len(set(ids)) or any(not isinstance(key, str) for key in ids):
        raise ValueError("Component must return unique string document IDs")
    if any(type(d.get("score")) not in (int, float) or not math.isfinite(d["score"]) for d in documents):
        raise ValueError("Component must return finite document scores")
    return {"output": {"documents": documents}, "observations": {"retrieval_results": ids},
            "observations_complete": ["retrieval_results"]}


def run_fts(request):
    return component_response(fts_retrieve(request["input"]))


def run_tfidf(request):
    return component_response(tfidf_rerank(request["input"]))


def http_component(request, kind):
    """Deployment registers the endpoint via environment; users never submit credentials."""
    import os
    import httpx
    import json
    from agenteval.adapters import validate_url
    from agenteval.config import Settings
    endpoint = os.environ[f"AGENTEVAL_{kind.upper()}_ENDPOINT"]
    validate_url(endpoint, Settings())
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        with client.stream("POST", endpoint, json=request["input"]) as reply:
            reply.raise_for_status()
            raw = bytearray()
            for chunk in reply.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 1_048_576:
                    raise ValueError("Component response exceeded 1 MiB")
            documents = json.loads(raw)["documents"]
    if not isinstance(documents, list) or len(documents) > 1000:
        raise ValueError("Invalid ranked component documents")
    if kind == "reranker" and {d["id"] for d in documents} - {d["id"] for d in request["input"]["documents"]}:
        raise ValueError("Reranker returned document IDs outside its candidates")
    return component_response(documents)
