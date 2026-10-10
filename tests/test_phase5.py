from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main


def _client_with_db(database):
    return database, TestClient(main.app)


def test_late_leaderboard_aggregates_by_employee_and_rank(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0501",
                "name": "Alpha",
                "email": "alpha@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0502",
                "name": "Bravo",
                "email": "bravo@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0503",
                "name": "Charlie",
                "email": "charlie@example.com",
                "department": "Support",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0504",
                "name": "Delta",
                "email": "delta@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0501",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 6, 4, 35, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc),
                "work_hours": 9.42,
                "late_minutes": 65,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0501",
                "date": "2026-07-07",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 7, 10, 15, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 7, 18, 0, tzinfo=timezone.utc),
                "work_hours": 8.75,
                "late_minutes": 45,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0502",
                "date": "2026-07-08",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 8, 10, 15, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 8, 18, 0, tzinfo=timezone.utc),
                "work_hours": 8.75,
                "late_minutes": 45,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0503",
                "date": "2026-07-09",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 9, 10, 15, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 9, 17, 0, tzinfo=timezone.utc),
                "work_hours": 7.75,
                "late_minutes": 45,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0504",
                "date": "2026-07-10",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 10, 9, 35, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc),
                "work_hours": 8.42,
                "late_minutes": 5,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0504",
                "date": "2026-07-11",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 11, 9, 35, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc),
                "work_hours": 8.42,
                "late_minutes": 5,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
        ]
    )

    response = client.get("/analytics/leaderboard/late?month=2026-07")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == "2026-07"
    assert [item["emp_code"] for item in body["items"]] == ["EMP0501", "EMP0502", "EMP0503", "EMP0504"]
    assert [item["rank"] for item in body["items"]] == [1, 2, 2, 4]
    assert [item["total_late_minutes"] for item in body["items"]] == [110, 45, 45, 10]
    assert [item["late_count"] for item in body["items"]] == [2, 1, 1, 2]


def test_late_leaderboard_limit_applies_after_ranking_and_department_filter(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0510",
                "name": "A",
                "email": "a@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0511",
                "name": "B",
                "email": "b@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0512",
                "name": "C",
                "email": "c@example.com",
                "department": "Support",
                "shift_start": "09:00",
                "shift_end": "17:00",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_many(
        [
            {"emp_code": "EMP0510", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc), "work_hours": 8.0, "late_minutes": 30, "overtime_minutes": 0, "half_day": False, "history": []},
            {"emp_code": "EMP0511", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc), "work_hours": 8.0, "late_minutes": 30, "overtime_minutes": 0, "half_day": False, "history": []},
            {"emp_code": "EMP0512", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc), "work_hours": 8.0, "late_minutes": 60, "overtime_minutes": 0, "half_day": False, "history": []},
        ]
    )

    limited = client.get("/analytics/leaderboard/late?month=2026-07&limit=1")
    assert limited.status_code == 200, limited.text
    assert [item["emp_code"] for item in limited.json()["items"]] == ["EMP0512"]

    tied_at_limit = client.get("/analytics/leaderboard/late?month=2026-07&limit=2")
    assert tied_at_limit.status_code == 200, tied_at_limit.text
    assert [item["emp_code"] for item in tied_at_limit.json()["items"]] == ["EMP0512", "EMP0510", "EMP0511"]
    assert [item["rank"] for item in tied_at_limit.json()["items"]] == [1, 2, 2]

    department_only = client.get("/analytics/leaderboard/late?month=2026-07&department=Engineering")
    assert department_only.status_code == 200, department_only.text
    payload = department_only.json()
    assert [item["emp_code"] for item in payload["items"]] == ["EMP0510", "EMP0511"]
    assert [item["rank"] for item in payload["items"]] == [1, 1]


def test_late_leaderboard_excludes_no_late_minutes_and_handles_invalid_input(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0520",
                "name": "On Time",
                "email": "ontime@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0521",
                "name": "Late One",
                "email": "lateone@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_many(
        [
            {"emp_code": "EMP0520", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 9, 35, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc), "work_hours": 8.42, "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []},
            {"emp_code": "EMP0521", "date": "2026-07-06", "status": "PRESENT", "punch_in": datetime(2026, 7, 6, 10, 15, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc), "work_hours": 7.75, "late_minutes": 45, "overtime_minutes": 0, "half_day": False, "history": []},
        ]
    )

    response = client.get("/analytics/leaderboard/late?month=2026-07")
    assert response.status_code == 200, response.text
    assert [item["emp_code"] for item in response.json()["items"]] == ["EMP0521"]

    invalid = client.get("/analytics/leaderboard/late?month=2026-13")
    assert invalid.status_code == 422

    invalid_limit = client.get("/analytics/leaderboard/late?month=2026-07&limit=0")
    assert invalid_limit.status_code == 422


def test_late_leaderboard_uses_weekend_records_and_empty_results(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0530",
                "name": "Weekend Late",
                "email": "weekend@example.com",
                "department": "Operations",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0531",
                "name": "No Lates",
                "email": "nolate@example.com",
                "department": "Operations",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )
    db.attendance_logs.insert_many(
        [
            {"emp_code": "EMP0530", "date": "2026-07-05", "status": "PRESENT", "punch_in": datetime(2026, 7, 5, 11, 0, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 5, 18, 0, tzinfo=timezone.utc), "work_hours": 7.0, "late_minutes": 30, "overtime_minutes": 0, "half_day": False, "history": []},
            {"emp_code": "EMP0531", "date": "2026-07-05", "status": "PRESENT", "punch_in": datetime(2026, 7, 5, 9, 35, tzinfo=timezone.utc), "punch_out": datetime(2026, 7, 5, 18, 0, tzinfo=timezone.utc), "work_hours": 8.42, "late_minutes": 5, "overtime_minutes": 0, "half_day": False, "history": []},
        ]
    )

    response = client.get("/analytics/leaderboard/late?month=2026-07")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["emp_code"] == "EMP0530"
    assert response.json()["items"][0]["total_late_minutes"] == 30

    empty = client.get("/analytics/leaderboard/late?month=2026-08")
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []
