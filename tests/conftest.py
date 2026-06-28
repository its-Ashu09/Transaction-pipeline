import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB = Path("test_pipeline.db").resolve()
UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="transaction-pipeline-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["UPLOAD_DIR"] = str(UPLOAD_DIR)
os.environ["GEMINI_API_KEY"] = ""

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


def pytest_sessionfinish() -> None:
    engine.dispose()
    TEST_DB.unlink(missing_ok=True)
    for path in UPLOAD_DIR.glob("*"):
        path.unlink(missing_ok=True)
    UPLOAD_DIR.rmdir()
