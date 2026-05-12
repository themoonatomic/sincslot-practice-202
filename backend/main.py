import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from backend.api.routes.register_routes import routes
from backend.core.config import settings
from backend.core.db_helper import db_helper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    # shutdown
    await db_helper.dispose()


app = FastAPI(lifespan=lifespan, title="Syncslot backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes)

if __name__ == "__main__":
    logging.info(f"Start server: {settings.run.port}")
    uvicorn.run("main:app", host=settings.run.host, port=settings.run.port, reload=True)
