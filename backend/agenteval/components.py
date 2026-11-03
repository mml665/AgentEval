"""Trusted standalone component registry; independent of business Agent execution."""
from agenteval.tracing import event
from agenteval.retrieval import run_fts, run_tfidf, http_component

CORPUS = [{"id": "policy-shipped", "text": "order shipped delivery tracking"},
          {"id": "policy-refund", "text": "refund return payment"}]


def retrieve(request):
    query = request["input"]["query"].lower().split()
    documents = request["input"].get("documents", CORPUS)
    ranked = sorted(documents, key=lambda d: -sum(word in d["text"].lower() for word in query))
    ranked = ranked[:request["input"].get("k", 5)]
    event("component.output", {"document_ids": [d["id"] for d in ranked]})
    return {"output": {"documents": ranked}, "observations": {"retrieval_results": [d["id"] for d in ranked]},
            "observations_complete": ["retrieval_results"]}


COMPONENTS = {
    "example.retriever": {"name": "Lexical Retriever", "kind": "retriever", "invoke": retrieve,
        "capabilities": ["retrieval_results"], "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 100},
            "documents": {"type": "array", "items": {"type": "object", "required": ["id", "text"],
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}}, "required": ["query"]}},
    "example.reranker": {"name": "TF-IDF Reranker", "kind": "reranker", "invoke": run_tfidf,
        "capabilities": ["retrieval_results"], "input_schema": {"type": "object", "required": ["query", "documents"],
            "properties": {"query": {"type": "string"}, "documents": {"type": "array", "minItems": 1,
                "items": {"type": "object", "required": ["id", "text"], "properties": {
                    "id": {"type": "string"}, "text": {"type": "string"}}}}}}},
}

from copy import deepcopy
import os
for key, name, kind, operation in (("sqlite.fts5", "SQLite FTS5 / BM25 Retriever", "retriever", run_fts),
                                  ("tfidf.cosine", "TF-IDF Cosine Reranker", "reranker", run_tfidf)):
    schema = deepcopy(COMPONENTS["example.reranker"]["input_schema"])
    schema["properties"]["k"] = {"type": "integer", "minimum": 1, "maximum": 100}
    COMPONENTS[key] = {"name": name, "kind": kind, "invoke": operation, "capabilities": ["retrieval_results"], "input_schema": schema}
for kind in ("retriever", "reranker"):
    if os.getenv(f"AGENTEVAL_{kind.upper()}_ENDPOINT"):
        COMPONENTS[f"http.{kind}"] = {**COMPONENTS["sqlite.fts5" if kind == "retriever" else "tfidf.cosine"],
            "name": f"Deployment HTTP {kind}", "invoke": lambda request, kind=kind: http_component(request, kind)}
