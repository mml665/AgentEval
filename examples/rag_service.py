"""Separate-process retrieval service: uvicorn examples.rag_service:app --port 9002."""
from fastapi import FastAPI, HTTPException
from agenteval.retrieval import fts_retrieve, tfidf_rerank

app = FastAPI(title="Independent RAG integration example")


def run(value, operation):
    try:
        return {"documents": operation(value)}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid query/documents") from exc


@app.post("/retrieve")
def retrieve(value: dict):
    return run(value, fts_retrieve)


@app.post("/rerank")
def rerank(value: dict):
    return run(value, tfidf_rerank)
