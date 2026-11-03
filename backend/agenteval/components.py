"""Trusted standalone component registry; independent of business Agent execution."""
from agenteval.tracing import event

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
    "example.reranker": {"name": "Lexical Reranker", "kind": "reranker", "invoke": retrieve,
        "capabilities": ["retrieval_results"], "input_schema": {"type": "object", "required": ["query", "documents"],
            "properties": {"query": {"type": "string"}, "documents": {"type": "array", "minItems": 1,
                "items": {"type": "object", "required": ["id", "text"], "properties": {
                    "id": {"type": "string"}, "text": {"type": "string"}}}}}}},
}
