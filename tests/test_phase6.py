from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import main


def _client_with_db(database):
    return database, TestClient(main.app)


def test_department_trend_returns_daily_rows_with_gaps_and_rates(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0601",
                "name": "A",
                "email": "a@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0602",
                "name": "B",
                "email": "b@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-03",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0603",
                "name": "C",
                "email": "c@example.com",
                "department": "Support",
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
                "emp_code": "EMP0601",
                "date": "2026-07-01",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 1, 9, 45, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 1, 18, 30, tzinfo=timezone.utc),
                "work_hours": 8.75,
                "late_minutes": 15,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0602",
                "date": "2026-07-03",
                "status": "WFH",
                "punch_in": datetime(2026, 7, 3, 9, 30, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 3, 12, 00, tzinfo=timezone.utc),
                "work_hours": 2.5,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": True,
                "history": [],
            },
        ]
    )

    response = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["department"] == "Engineering"
    assert [item["date"] for item in payload["items"]] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
        "2026-07-06",
        "2026-07-07",
    ]
    assert payload["items"][0]["headcount"] == 1
    assert payload["items"][0]["present_count"] == 1.0
    assert payload["items"][0]["late_count"] == 1
    assert payload["items"][0]["attendance_rate"] == 1.0
    assert payload["items"][1]["is_working_day"] is True
    assert payload["items"][1]["attendance_rate"] == 0.0
    assert payload["items"][2]["headcount"] == 2
    assert payload["items"][2]["present_count"] == 0.5
    assert payload["items"][2]["attendance_rate"] == 0.25
    assert payload["items"][3]["is_working_day"] is False
    assert payload["items"][3]["attendance_rate"] is None
    assert payload["items"][3]["present_count"] == 0.0
    assert payload["items"][4]["headcount"] == 2
    assert payload["items"][4]["attendance_rate"] is None
    assert payload["items"][5]["attendance_rate"] == 0.0
    assert payload["items"][6]["attendance_rate"] == 0.0
    assert payload["items"][2]["moving_avg_7d"] == 0.4167


def test_department_trend_rejects_unknown_department_and_bad_ranges(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [{
            "emp_code": "EMP0610",
            "name": "Only",
            "email": "only@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01",
            "created_at": datetime.now(timezone.utc),
        }]
    )

    missing = client.get("/analytics/departments/Operations/trend?from=2026-07-01&to=2026-07-02")
    assert missing.status_code == 404

    invalid_order = client.get("/analytics/departments/Engineering/trend?from=2026-07-10&to=2026-07-01")
    assert invalid_order.status_code == 422

    too_long = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-10-01")
    assert too_long.status_code == 422


def test_department_trend_uses_join_dates_and_no_logs(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": "EMP0620",
                "name": "Joined Early",
                "email": "early@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-01",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0621",
                "name": "Joined Later",
                "email": "later@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-05",
                "created_at": datetime.now(timezone.utc),
            },
            {
                "emp_code": "EMP0622",
                "name": "Future",
                "email": "future@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-10",
                "created_at": datetime.now(timezone.utc),
            },
        ]
    )

    response = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-07")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    by_date = {item["date"]: item for item in items}
    assert by_date["2026-07-01"]["headcount"] == 1
    assert by_date["2026-07-05"]["headcount"] == 2
    assert by_date["2026-07-06"]["headcount"] == 2
    assert by_date["2026-07-07"]["headcount"] == 2


def test_department_trend_moving_average_starts_at_from_and_uses_seven_rows(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_one(
        {
            "emp_code": "EMP0630",
            "name": "Window",
            "email": "window@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01",
            "created_at": datetime.now(timezone.utc),
        }
    )
    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0630",
                "date": "2026-07-03",
                "status": "ABSENT",
                "punch_in": None,
                "punch_out": None,
                "work_hours": None,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0630",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": datetime(2026, 7, 6, 4, 30, tzinfo=timezone.utc),
                "punch_out": datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
        ]
    )

    response = client.get("/analytics/departments/Engineering/trend?from=2026-07-04&to=2026-07-11")
    assert response.status_code == 200, response.text
    by_date = {row["date"]: row for row in response.json()["items"]}
    assert by_date["2026-07-04"]["moving_avg_7d"] is None
    assert by_date["2026-07-06"]["moving_avg_7d"] == 1.0
    assert by_date["2026-07-11"]["moving_avg_7d"] == 0.2


def test_department_trend_rate_uses_half_up_rounding(analytics_database):
    db, client = _client_with_db(analytics_database)
    db.employees.insert_many(
        [
            {
                "emp_code": f"EMP064{index:02d}",
                "name": f"Employee {index}",
                "email": f"employee{index}@example.com",
                "department": "Engineering",
                "shift_start": "09:30",
                "shift_end": "18:30",
                "joined_on": "2026-07-06",
                "created_at": datetime.now(timezone.utc),
            }
            for index in range(32)
        ]
    )
    db.attendance_logs.insert_one(
        {
            "emp_code": "EMP06400",
            "date": "2026-07-06",
            "status": "PRESENT",
            "punch_in": datetime(2026, 7, 6, 4, 30, tzinfo=timezone.utc),
            "punch_out": datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc),
            "work_hours": 9.0,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
            "history": [],
        }
    )

    response = client.get("/analytics/departments/Engineering/trend?from=2026-07-06&to=2026-07-06")
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["headcount"] == 32
    assert row["attendance_rate"] == 0.0313
    assert row["moving_avg_7d"] == 0.0313
