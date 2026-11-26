import os
from uuid import uuid4

import chromadb
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

app = FastAPI(title="${{ values.name }}")
vector_client = chromadb.PersistentClient(path=os.getenv("VECTOR_DB_PATH", ".chroma"))
documents = vector_client.get_or_create_collection(name="documents")


class DocumentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


def model_client() -> OpenAI:
    api_key = os.getenv("MODEL_API_KEY")
    model = os.getenv("MODEL_NAME")
    if not api_key or not model:
        raise HTTPException(status_code=503, detail="Configure MODEL_API_KEY and MODEL_NAME")
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("MODEL_BASE_URL") or None,
        timeout=30.0,
        max_retries=2,
    )


def embed(client: OpenAI, text: str) -> list[float]:
    model = os.getenv("EMBEDDING_MODEL")
    if not model:
        raise HTTPException(status_code=503, detail="Configure EMBEDDING_MODEL")
    result = client.embeddings.create(model=model, input=text)
    return result.data[0].embedding


@app.get("/health")
async def health():
    return {"status": "ok", "documents": documents.count()}


@app.post("/documents")
def add_document(request: DocumentRequest):
    client = model_client()
    document_id = str(uuid4())
    documents.add(ids=[document_id], documents=[request.content], embeddings=[embed(client, request.content)])
    return {"id": document_id}


@app.post("/query")
def query(request: QueryRequest):
    if documents.count() == 0:
        raise HTTPException(status_code=409, detail="Add documents before querying")
    client = model_client()
    matches = documents.query(query_embeddings=[embed(client, request.question)], n_results=min(4, documents.count()))
    context = "\n\n".join(matches.get("documents", [[]])[0])
    answer = client.chat.completions.create(
        model=os.environ["MODEL_NAME"],
        messages=[
            {"role": "system", "content": "Answer only from the supplied context. If it is insufficient, say so."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {request.question}"},
        ],
    )
    return {"answer": answer.choices[0].message.content or "", "sources": matches.get("ids", [[]])[0]}