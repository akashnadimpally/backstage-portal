from fastapi import FastAPI
from pydantic import BaseModel, Field

from agents import run_workflow

app = FastAPI(title="${{ values.name }}")


class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=12000)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run")
async def run(request: TaskRequest):
    results = await run_workflow(request.task)
    return {"results": [result.__dict__ for result in results]}