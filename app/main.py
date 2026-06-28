from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.jobs import router as jobs_router
from app.config import get_settings
from app.database import Base, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Asynchronous CSV cleaning, anomaly detection, LLM classification, "
        "and spending summaries."
    ),
    lifespan=lifespan,
)
app.include_router(jobs_router)


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}
