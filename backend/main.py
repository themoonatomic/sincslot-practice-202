import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from backend.api.routes.register_routes import routes
from backend.core.config import settings
from backend.core.db_helper import db_helper
from backend.core.metrics import (
    active_requests,
    http_request_duration_seconds,
    http_requests_total,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await db_helper.dispose()


app = FastAPI(lifespan=lifespan, title="Syncslot backend")


@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    method = request.method
    endpoint = request.url.path
    start_time = time.time()

    active_requests.inc()

    try:
        response = await call_next(request)
        status_code = str(response.status_code)
        return response
    except Exception:
        status_code = "500"
        raise
    finally:
        duration = time.time() - start_time

        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status=status_code,
        ).inc()

        http_request_duration_seconds.labels(
            endpoint=endpoint,
        ).observe(duration)

        active_requests.dec()


@app.get("/metrics")
async def metrics() -> Response:
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes)


if __name__ == "__main__":
    logging.info(f"Start server: {settings.run.port}")
    uvicorn.run(
        "main:app",
        host=settings.run.host,
        port=settings.run.port,
        reload=True,
    )