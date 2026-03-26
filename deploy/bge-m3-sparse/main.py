"""
BGE-M3 sparse embedding sidecar.
Serves sparse token weights via FlagEmbedding.
Used by knowledge-ingest for hybrid dense+sparse retrieval.
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

MODEL_NAME = os.getenv("MODEL_NAME", "BAAI/bge-m3")
HF_HOME = os.getenv("HF_HOME", "/models")

_model = None


def _load_model():
    from FlagEmbedding import BGEM3FlagModel
    logger.info("Loading BGE-M3 sparse model from %s...", HF_HOME)
    return BGEM3FlagModel(MODEL_NAME, use_fp16=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _model
    import asyncio
    loop = asyncio.get_event_loop()
    _model = await loop.run_in_executor(None, _load_model)
    logger.info("BGE-M3 sparse model ready.")
    yield


app = FastAPI(title="bge-m3-sparse", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    indices: list[int]
    values: list[float]


@app.post("/embed_sparse", response_model=EmbedResponse)
async def embed_sparse(req: EmbedRequest) -> EmbedResponse:
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _compute_sparse, req.text)
    return EmbedResponse(indices=result["indices"], values=result["values"])


def _compute_sparse(text: str) -> dict:
    output = _model.encode(
        [text],
        return_dense=False,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    sparse = output["lexical_weights"][0]
    # sparse is a dict of {token_id: weight}
    indices = [int(k) for k in sparse.keys()]
    values = [float(v) for v in sparse.values()]
    return {"indices": indices, "values": values}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model": MODEL_NAME}
