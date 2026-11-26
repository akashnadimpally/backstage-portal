import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from pipeline import TrainingService

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


class TextRecordEvent(BaseModel):
    record_id: str | None = Field(default=None, min_length=1, max_length=200)
    record: dict[str, Any]


class PredictionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.training = TrainingService.from_env()

    async def retrain_loop():
        interval = max(1, int(os.getenv("RETRAIN_CHECK_SECONDS", "15")))
        while True:
            try:
                result = await asyncio.to_thread(application.state.training.retrain_if_due)
                if result["status"] == "trained":
                    logger.info("Trained NLP model version %s: %s", result["model_version"], result["metrics"])
            except Exception:
                logger.exception("Streaming NLP training/evaluation failed")
            await asyncio.sleep(interval)

    task = asyncio.create_task(retrain_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="${{ values.name }}", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    return {"status": "ok", "model_ready": request.app.state.training.model_path.exists()}


@app.post("/records", status_code=202)
async def ingest_record(event: TextRecordEvent, request: Request):
    try:
        record_id = request.app.state.training.add_record(event.record_id, event.record)
        return {"accepted": True, "record_id": record_id}
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/model/status")
async def model_status(request: Request):
    return request.app.state.training.status()


@app.post("/predict")
async def predict(payload: PredictionRequest, request: Request):
    try:
        result = await asyncio.to_thread(request.app.state.training.predict, payload.text)
        return {"label": result}
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error