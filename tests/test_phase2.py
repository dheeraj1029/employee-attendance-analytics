from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import mongomock
from fastapi.testclient import TestClient

from app import main


def _client_with_db(monkeypatch):
    mock_db = mongomock.MongoClient().db
    monkeypatch.setattr(main, "db", mock_db)
    main.ensure_indexes()
    return TestClient(main.app)


def _ms(dt_utc: datetime) -> int:
    return int(dt_utc.timestamp() * 1000)


def test_punch_in_creates_record_and_duplicate_conflict(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0001",
            "name": "Asha Rao",
            "email": "asha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )

    response = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["date"] == "2026-07-06"
    assert body["status"] == "PRESENT"
    assert body["late_minutes"] == 0
    assert body["punch_in"] == _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))

    duplicate = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0001", "punched_at": _ms(datetime(2026, 7, 6, 3, 59, tzinfo=timezone.utc))},
    )
    assert duplicate.status_code == 409


def test_punch_instants_are_truncated_to_whole_seconds(monkeypatch):
    client = _client_with_db(monkeypatch)
    explicit_null = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0011", "punched_at": None},
    )
    assert explicit_null.status_code == 422

    client.post(
        "/employees",
        json={
            "emp_code": "EMP0011",
            "name": "Fractional",
            "email": "fractional@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )
    fractional_ms = _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc)) + 987
    response = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0011", "punched_at": fractional_ms},
    )
    assert response.status_code == 201, response.text
    assert response.json()["punch_in"] == _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))


def test_punch_in_uses_overnight_date_and_rejects_missing_employee(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0002",
            "name": "Night Crew",
            "email": "night@example.com",
            "department": "Operations",
            "shift_start": "22:00",
            "shift_end": "06:00",
            "joined_on": "2026-01-05",
        },
    )
    response = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0002", "punched_at": _ms(datetime(2026, 7, 7, 18, 30, tzinfo=timezone.utc))},
    )
    assert response.status_code == 201, response.text
    assert response.json()["date"] == "2026-07-07"

    missing = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP9999", "punched_at": _ms(datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc))},
    )
    assert missing.status_code == 404


def test_punch_out_calculates_work_hours_and_overtime(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0003",
            "name": "Late Worker",
            "email": "late@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )

    punch_in = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0003", "punched_at": _ms(datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc))},
    )
    assert punch_in.status_code == 201

    punch_out = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0003", "punched_at": _ms(datetime(2026, 7, 6, 13, 40, tzinfo=timezone.utc))},
    )
    assert punch_out.status_code == 200, punch_out.text
    body = punch_out.json()
    assert body["late_minutes"] == 35
    assert body["overtime_minutes"] == 40
    assert body["work_hours"] == 9.08
    assert body["half_day"] is False


def test_punch_out_rejects_invalid_window(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0004",
            "name": "Half Day",
            "email": "half@example.com",
            "department": "Support",
            "shift_start": "09:00",
            "shift_end": "17:00",
            "joined_on": "2026-01-05",
        },
    )
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0004", "punched_at": _ms(datetime(2026, 7, 6, 3, 10, tzinfo=timezone.utc))},
    )

    invalid = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0004", "punched_at": _ms(datetime(2026, 7, 6, 3, 5, tzinfo=timezone.utc))},
    )
    assert invalid.status_code == 422


def test_punch_out_does_not_close_a_future_punch_in(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0012",
            "name": "Future Punch",
            "email": "future@example.com",
            "department": "Support",
            "shift_start": "09:00",
            "shift_end": "17:00",
            "joined_on": "2026-01-05",
        },
    )
    punch_in_ms = _ms(datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc))
    client.post("/attendance/punch-in", json={"emp_code": "EMP0012", "punched_at": punch_in_ms})

    too_early = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0012", "punched_at": punch_in_ms - 1000},
    )
    assert too_early.status_code == 422


def test_half_day_calculated_correctly(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0005",
            "name": "Short Day",
            "email": "short@example.com",
            "department": "Support",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0005", "punched_at": _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))},
    )

    response = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0005", "punched_at": _ms(datetime(2026, 7, 6, 8, 30, tzinfo=timezone.utc))},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["work_hours"] == 4.53
    assert body["half_day"] is False

    short = client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0005", "punched_at": _ms(datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc))},
    )
    assert short.status_code == 201
    short_out = client.post(
        "/attendance/punch-out",
        json={"emp_code": "EMP0005", "punched_at": _ms(datetime(2026, 7, 7, 7, 58, tzinfo=timezone.utc))},
    )
    payload = short_out.json()
    assert payload["work_hours"] == 4.0
    assert payload["half_day"] is True


def test_attendance_listing_filters_paginate_and_sorts(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0101",
            "name": "Alpha",
            "email": "alpha@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0102",
            "name": "Bravo",
            "email": "bravo@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )

    ts1 = _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))
    ts2 = _ms(datetime(2026, 7, 7, 3, 58, tzinfo=timezone.utc))
    ts3 = _ms(datetime(2026, 7, 8, 3, 58, tzinfo=timezone.utc))

    for emp_code, punched_at in [("EMP0101", ts1), ("EMP0102", ts2), ("EMP0101", ts3)]:
        client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": punched_at})

    response = client.get(
        "/attendance",
        params={"emp_code": "EMP0101", "date_from": "2026-07-06", "date_to": "2026-07-08", "page": 1, "page_size": 10},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["items"][0]["date"] == "2026-07-08"
    assert payload["items"][1]["date"] == "2026-07-06"

    bad_range = client.get("/attendance", params={"date_from": "2026-07-08", "date_to": "2026-07-06"})
    assert bad_range.status_code == 422


def test_regularization_updates_history_and_recomputes_fields(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0103",
            "name": "Corrector",
            "email": "corrector@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )
    client.post(
        "/attendance/punch-in",
        json={"emp_code": "EMP0103", "punched_at": _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))},
    )

    response = client.patch(
        "/attendance/EMP0103/2026-07-06",
        json={
            "status": "ABSENT",
            "reason": "Employee was off sick",
            "regularized_by": "hr-team",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ABSENT"
    assert body["punch_in"] is None
    assert body["punch_out"] is None
    assert len(body["history"]) == 1
    assert body["history"][0]["by"] == "hr-team"
    assert body["history"][0]["changes"]["status"] == {"from": "PRESENT", "to": "ABSENT"}

    no_op = client.patch(
        "/attendance/EMP0103/2026-07-06",
        json={
            "status": "ABSENT",
            "reason": "Employee was off sick",
            "regularized_by": "hr-team",
        },
    )
    assert no_op.status_code == 422


def test_regularization_handles_missing_history_and_records_derived_changes(monkeypatch):
    mock_db = mongomock.MongoClient().db
    monkeypatch.setattr(main, "db", mock_db)
    main.ensure_indexes()
    client = TestClient(main.app)
    mock_db.employees.insert_one(
        {
            "emp_code": "EMP0013",
            "name": "Seeded",
            "email": "seeded@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
            "created_at": datetime.now(timezone.utc),
        }
    )
    mock_db.attendance_logs.insert_one(
        {
            "emp_code": "EMP0013",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc),
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    )

    response = client.patch(
        "/attendance/EMP0013/2026-07-06",
        json={
            "punch_in": _ms(datetime(2026, 7, 6, 4, 15, tzinfo=timezone.utc)),
            "reason": "Corrected punch time",
            "regularized_by": "hr-team",
        },
    )
    assert response.status_code == 200, response.text
    changes = response.json()["history"][0]["changes"]
    assert changes["punch_in"]["from"] == _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))
    assert changes["late_minutes"] == {"from": 0, "to": 15}


def test_attendance_and_regularization_reject_invalid_calendar_dates(monkeypatch):
    client = _client_with_db(monkeypatch)
    invalid_list_date = client.get("/attendance?date_from=2026-02-30")
    assert invalid_list_date.status_code == 422

    invalid_patch_date = client.patch(
        "/attendance/EMP0014/2026-02-30",
        json={"status": "ABSENT", "reason": "Date correction", "regularized_by": "hr-team"},
    )
    assert invalid_patch_date.status_code == 422


def test_attendance_listing_serializes_seeded_default_fields(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(main, "db", db)
    main.ensure_indexes()
    db.attendance_logs.insert_one(
        {
            "emp_code": "EMP0015",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
        }
    )

    response = TestClient(main.app).get("/attendance")
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["late_minutes"] == 0
    assert row["overtime_minutes"] == 0
    assert row["half_day"] is False
    assert row["history"] == []


def test_concurrent_punch_in_and_punch_out_are_atomic(monkeypatch):
    client = _client_with_db(monkeypatch)
    client.post(
        "/employees",
        json={
            "emp_code": "EMP0104",
            "name": "Concurrent",
            "email": "concurrent@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-05",
        },
    )

    ts = _ms(datetime(2026, 7, 6, 3, 58, tzinfo=timezone.utc))
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post("/attendance/punch-in", json={"emp_code": "EMP0104", "punched_at": ts}), range(2)))
    assert sum(1 for item in responses if item.status_code == 201) == 1
    assert sum(1 for item in responses if item.status_code == 409) == 1

    out_ts = _ms(datetime(2026, 7, 6, 13, 10, tzinfo=timezone.utc))
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post("/attendance/punch-out", json={"emp_code": "EMP0104", "punched_at": out_ts}), range(2)))
    assert sum(1 for item in responses if item.status_code == 200) == 1
    assert sum(1 for item in responses if item.status_code == 409) == 1
