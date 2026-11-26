import os

from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

app = FastAPI(title="${{ values.name }}")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest):
    api_key = os.getenv("MODEL_API_KEY")
    model = os.getenv("MODEL_NAME")
    if not api_key or not model:
        raise HTTPException(status_code=503, detail="Configure MODEL_API_KEY and MODEL_NAME")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("MODEL_BASE_URL") or None,
        timeout=30.0,
        max_retries=2,
    )
    result = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": request.message}],
    )
    return {"response": result.choices[0].message.content or ""}