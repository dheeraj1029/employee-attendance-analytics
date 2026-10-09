from datetime import datetime, timezone

import mongomock
from fastapi.testclient import TestClient

from app import main


def _client_with_db(monkeypatch):
    mock_db = mongomock.MongoClient().db
    monkeypatch.setattr(main, "db", mock_db)
    main.ensure_indexes()
    return mock_db, TestClient(main.app)


def test_department_summary_aggregates_counts_and_rounding(monkeypatch):
    db, client = _client_with_db(monkeypatch)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0401",
                "name": "Eng One",
                "email": "eng1@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0402",
                "name": "Eng Two",
                "email": "eng2@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-10",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0403",
                "name": "Future Eng",
                "email": "futureeng@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-08-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0404",
                "name": "Support One",
                "email": "support1@example.com",
                "department": "Support",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0405",
                "name": "Future Support",
                "email": "futuresupport@example.com",
                "department": "Support",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-08-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )

    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0401",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc),
                "work_hours": 9.08,
                "late_minutes": 35,
                "overtime_minutes": 40,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0401",
                "date": "2026-07-07",
                "status": "WFH",
                "punch_in": datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 7, 7, 58, tzinfo=timezone.utc),
                "work_hours": 4.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": True,
                "history": [],
            },
            {
                "emp_code": "EMP0401",
                "date": "2026-07-11",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 11, 3, 58, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 11, 12, 58, tzinfo=timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 15,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0401",
                "date": "2026-07-18",
                "status": "LEAVE",
                "punch_in": None,
                "punch_out": None,
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0404",
                "date": "2026-07-08",
                "status": "ON_DUTY",
                "punch_in": datetime(2026, 7, 8, 3, 58, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 8, 12, 58, tzinfo=timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0404",
                "date": "2026-07-13",
                "status": "LEAVE",
                "punch_in": None,
                "punch_out": None,
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
        ]
    )

    response = client.get("/analytics/departments/summary?month=2026-07")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == "2026-07"
    assert [item["department"] for item in body["items"]] == ["Engineering", "Support"]

    engineering = body["items"][0]
    assert engineering["headcount"] == 2
    assert engineering["present_days"] == 1.5
    assert engineering["avg_work_hours"] == 7.36
    assert engineering["late_count"] == 2
    assert engineering["total_late_minutes"] == 50
    assert engineering["leave_count"] == 1
    assert engineering["on_duty_count"] == 0

    support = body["items"][1]
    assert support["headcount"] == 1
    assert support["present_days"] == 1.0
    assert support["avg_work_hours"] == 9.0
    assert support["late_count"] == 0
    assert support["total_late_minutes"] == 0
    assert support["leave_count"] == 1
    assert support["on_duty_count"] == 1


def test_department_summary_optional_department_filter_and_no_matching_department(monkeypatch):
    db, client = _client_with_db(monkeypatch)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0410",
                "name": "Eng One",
                "email": "eng10@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0411",
                "name": "Support One",
                "email": "support10@example.com",
                "department": "Support",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_one(
        {
            "emp_code": "EMP0410",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 12, 58, tzinfo=timezone.utc),
            "work_hours": 9.0,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }
    )

    filtered = client.get("/analytics/departments/summary?month=2026-07&department=Engineering")
    assert filtered.status_code == 200, filtered.text
    payload = filtered.json()
    assert payload["items"][0]["department"] == "Engineering"
    assert payload["items"][0]["headcount"] == 1
    assert payload["items"][0]["present_days"] == 1.0

    empty = client.get("/analytics/departments/summary?month=2026-07&department=NoSuchDept")
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []


def test_department_summary_ignores_future_joiners_and_keeps_zero_log_departments(monkeypatch):
    db, client = _client_with_db(monkeypatch)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0420",
                "name": "Joined in Month",
                "email": "july@example.com",
                "department": "Ops",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-15",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0421",
                "name": "Future Joiner",
                "email": "future@example.com",
                "department": "Ops",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-08-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0422",
                "name": "Zero Log Dept",
                "email": "zerolog@example.com",
                "department": "Finance",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_one(
        {
            "emp_code": "EMP0420",
            "date": "2026-07-20",
            "status": "WFH",
            "punch_in": datetime(2026, 7, 20, 4, 0, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
            "work_hours": 4.0,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": True,
            "history": [],
        }
    )

    response = client.get("/analytics/departments/summary?month=2026-07")
    assert response.status_code == 200, response.text
    body = response.json()
    items_by_dept = {item["department"]: item for item in body["items"]}
    assert items_by_dept["Ops"]["headcount"] == 1
    assert items_by_dept["Ops"]["present_days"] == 0.5
    assert items_by_dept["Finance"]["headcount"] == 1
    assert items_by_dept["Finance"]["present_days"] == 0.0
    assert items_by_dept["Finance"]["avg_work_hours"] is None
