from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import main


def _client_with_db(database):
    return database


def _working_days_in_month(joined_on: str, month: str) -> int:
    year, month_num = map(int, month.split("-"))
    first = date(year, month_num, 1)
    if month_num == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month_num + 1, 1) - timedelta(days=1)
    joined = date.fromisoformat(joined_on)
    start = max(first, joined)
    total = 0
    current = start
    while current <= last:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def test_employee_monthly_summary_uses_stored_derived_values(analytics_database):
    db = _client_with_db(analytics_database)
    client = TestClient(main.app)
    db.employees.insert_one(
        {
            "emp_code": "EMP0301",
            "name": "Monthly Sum",
            "email": "monthly@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01",
            "created_at": main.datetime.now(main.timezone.utc),
        }
    )

    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0301",
                "date": "2026-07-06",
                "status": "PRESENT",
                "punch_in": main.datetime(2026, 7, 6, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 6, 13, 40, tzinfo=main.timezone.utc),
                "work_hours": 9.08,
                "late_minutes": 35,
                "overtime_minutes": 40,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0301",
                "date": "2026-07-07",
                "status": "WFH",
                "punch_in": main.datetime(2026, 7, 7, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 7, 7, 58, tzinfo=main.timezone.utc),
                "work_hours": 4.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": True,
                "history": [],
            },
            {
                "emp_code": "EMP0301",
                "date": "2026-07-11",
                "status": "PRESENT",
                "punch_in": main.datetime(2026, 7, 11, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 11, 12, 58, tzinfo=main.timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 15,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0301",
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
        ]
    )

    response = client.get("/analytics/employees/EMP0301/monthly?month=2026-07")
    assert response.status_code == 200, response.text
    body = response.json()
    working_days = _working_days_in_month("2026-07-01", "2026-07")
    assert body["emp_code"] == "EMP0301"
    assert body["month"] == "2026-07"
    assert body["working_days"] == working_days
    assert body["present_days"] == 1.5
    assert body["leave_days"] == 1
    assert body["late_count"] == 2
    assert body["total_late_minutes"] == 50
    assert body["total_overtime_minutes"] == 40
    expected_pct = float(
        main.round_half_up((main.Decimal("1.5") / main.Decimal(str(working_days)) * main.Decimal("100")), "0.0001")
    )
    assert body["attendance_pct"] == expected_pct


def test_employee_monthly_summary_handles_missing_records_invalid_month_and_unknown_employee(analytics_database):
    db = _client_with_db(analytics_database)
    client = TestClient(main.app)
    db.employees.insert_one(
        {
            "emp_code": "EMP0302",
            "name": "No Logs",
            "email": "nologs@example.com",
            "department": "Support",
            "shift_start": "09:00",
            "shift_end": "17:00",
            "joined_on": "2026-08-01",
            "created_at": main.datetime.now(main.timezone.utc),
        }
    )

    response = client.get("/analytics/employees/EMP0302/monthly?month=2026-07")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["working_days"] == 0
    assert payload["present_days"] == 0.0
    assert payload["leave_days"] == 0
    assert payload["late_count"] == 0
    assert payload["total_late_minutes"] == 0
    assert payload["total_overtime_minutes"] == 0
    assert payload["attendance_pct"] is None

    invalid = client.get("/analytics/employees/EMP0302/monthly?month=2026-13")
    assert invalid.status_code == 422

    missing = client.get("/analytics/employees/EMP9999/monthly?month=2026-07")
    assert missing.status_code == 404


def test_employee_monthly_summary_counts_mid_month_joining_and_ignores_weekends(analytics_database):
    db = _client_with_db(analytics_database)
    client = TestClient(main.app)
    db.employees.insert_one(
        {
            "emp_code": "EMP0303",
            "name": "Mid Join",
            "email": "midjoin@example.com",
            "department": "Operations",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-15",
            "created_at": main.datetime.now(main.timezone.utc),
        }
    )
    db.attendance_logs.insert_many(
        [
            {
                "emp_code": "EMP0303",
                "date": "2026-07-15",
                "status": "PRESENT",
                "punch_in": main.datetime(2026, 7, 15, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 15, 12, 58, tzinfo=main.timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0303",
                "date": "2026-07-18",
                "status": "PRESENT",
                "punch_in": main.datetime(2026, 7, 18, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 18, 12, 58, tzinfo=main.timezone.utc),
                "work_hours": 9.0,
                "late_minutes": 30,
                "overtime_minutes": 15,
                "half_day": False,
                "history": [],
            },
            {
                "emp_code": "EMP0303",
                "date": "2026-07-20",
                "status": "WFH",
                "punch_in": main.datetime(2026, 7, 20, 3, 58, tzinfo=main.timezone.utc),
                "punch_out": main.datetime(2026, 7, 20, 7, 58, tzinfo=main.timezone.utc),
                "work_hours": 4.0,
                "late_minutes": 0,
                "overtime_minutes": 0,
                "half_day": True,
                "history": [],
            },
        ]
    )

    response = client.get("/analytics/employees/EMP0303/monthly?month=2026-07")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["working_days"] == _working_days_in_month("2026-07-15", "2026-07")
    assert body["present_days"] == 1.5
    assert body["late_count"] == 1
    assert body["total_late_minutes"] == 30
    assert body["total_overtime_minutes"] == 15
