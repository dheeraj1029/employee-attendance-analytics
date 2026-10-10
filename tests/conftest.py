import uuid

import pytest
import os
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app import main


@pytest.fixture
def analytics_database(monkeypatch):
    client = MongoClient(
    os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017"),
    serverSelectionTimeoutMS=3000,
)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        pytest.skip("local MongoDB is unavailable for aggregation integration tests")

    database = client[f"attendance_analytics_{uuid.uuid4().hex}"]
    monkeypatch.setattr(main, "db", database)
    main.ensure_indexes()
    yield database
    client.drop_database(database.name)
    client.close()
