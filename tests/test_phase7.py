import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app import main


@pytest.fixture
def live_mongo_client(monkeypatch):
    client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        pytest.skip("local MongoDB is unavailable")

    database = client[f"attendance_phase7_{uuid.uuid4().hex}"]
    monkeypatch.setattr(main, "db", database)
    main.ensure_indexes()
    main.ensure_indexes()
    database.employees.insert_many(
        [
            {
                "emp_code": "EMP0701",
                "name": "Alice",
                "email": "alice@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0702",
                "name": "Bob",
                "email": "bob@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-02",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0703",
                "name": "Casey",
                "email": "casey@example.com",
                "department": "Support",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    database.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0701",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 6, 4, 40, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc),
                "work_hours": 8.83,
                "late_minutes": 10,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0702",
                "date": "2026-07-06",
                "status": "WFH",
                "punch_in": datetime(2026, 7, 6, 4, 30, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0703",
                "date": "2026-07-07",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 7, 4, 45, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 7, 13, 30, tzinfo=timezone.utc),
                "work_hours": 8.75,
                "late_minutes": 15,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
        ]
    )
    yield client, database
    client.drop_database(database.name)
    client.close()


def _contains_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def test_explain_runs_all_supported_operations_with_real_execution_stats(live_mongo_client):
    _, database = live_mongo_client
    client = TestClient(main.app)
    requests = [
        ("/admin/explain/attendance_list?emp_code=EMP0701&date_from=2026-07-01&date_to=2026-07-31&page=1&page_size=10", "attendance_list", "attendance_logs"),
        ("/admin/explain/employee_monthly?emp_code=EMP0701&month=2026-07", "employee_monthly", "employees"),
        ("/admin/explain/department_summary?month=2026-07&department=Engineering", "department_summary", "employees"),
        ("/admin/explain/late_leaderboard?month=2026-07&limit=10&department=Engineering", "late_leaderboard", "attendance_logs"),
        ("/admin/explain/department_trend?department=Engineering&from=2026-07-01&to=2026-07-10", "department_trend", "employees"),
    ]

    for url, endpoint, collection in requests:
        response = client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["endpoint"] == endpoint
        assert body["collection"] == collection
        assert _contains_key(body["explain"], "executionStats")
        serialized_explain = json.dumps(body["explain"])
        assert "IXSCAN" in serialized_explain
        assert "COLLSCAN" not in serialized_explain

    assert database.command("ping")["ok"] == 1.0


def test_explain_validates_supported_names_and_required_parameters(live_mongo_client):
    _, _ = live_mongo_client
    client = TestClient(main.app)

    unsupported = client.get("/admin/explain/not_an_endpoint")
    assert unsupported.status_code == 422

    missing_month = client.get("/admin/explain/employee_monthly?emp_code=EMP0701")
    assert missing_month.status_code == 422

    missing_trend_dates = client.get("/admin/explain/department_trend?department=Engineering")
    assert missing_trend_dates.status_code == 422

    invalid_attendance_range = client.get(
        "/admin/explain/attendance_list?date_from=2026-07-10&date_to=2026-07-01"
    )
    assert invalid_attendance_range.status_code == 422


def test_explain_does_not_expose_database_error_details(live_mongo_client, monkeypatch):
    _, _ = live_mongo_client

    def fail_explain(*args, **kwargs):
        raise PyMongoError("private connection detail")

    monkeypatch.setattr(main.db, "command", fail_explain)
    response = TestClient(main.app).get(
        "/admin/explain/employee_monthly?emp_code=EMP0701&month=2026-07"
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "MongoDB explain failed"
    assert "private connection detail" not in response.text
