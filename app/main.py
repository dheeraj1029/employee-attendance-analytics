"""Employee Attendance & Analytics API.

This file contains the application for the HROne attendance API.
"""
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, status as http_status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient, errors
from pymongo.errors import PyMongoError
from bson import json_util

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = client[MONGO_DB]

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

IST = timezone(timedelta(hours=5, minutes=30))
VALID_STATUS = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
PRESENCE_STATUS = {"PRESENT", "WFH", "ON_DUTY"}


def now_epoch_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def normalize_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def as_epoch_ms(value: Any) -> Any:
    value = normalize_datetime(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.astimezone(timezone.utc).timestamp() * 1000)
    if isinstance(value, (int, float)):
        return int(value)
    return value


def serialize_employee(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    item.pop("_id", None)
    if "created_at" in item:
        item["created_at"] = as_epoch_ms(item["created_at"])
    return item


def serialize_nested(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_epoch_ms(value)
    if isinstance(value, list):
        return [serialize_nested(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_nested(item) for key, item in value.items()}
    return value


def serialize_attendance_record(doc: dict[str, Any]) -> dict[str, Any]:
    item = dict(doc)
    item.pop("_id", None)
    for key in ("punch_in", "punch_out", "created_at"):
        if key in item:
            item[key] = as_epoch_ms(item[key])
    if "history" in item:
        item["history"] = serialize_nested(item["history"])
    return item


def month_range(month: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must be in YYYY-MM format")
    year, month_num = map(int, month.split("-"))
    first_day = date(year, month_num, 1)
    if month_num == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month_num + 1, 1) - timedelta(days=1)
    return first_day, last_day


def working_days_in_month(joined_on: str, month: str) -> int:
    joined = date.fromisoformat(joined_on)
    start_day, end_day = month_range(month)
    if joined > end_day:
        return 0
    start_date = max(joined, start_day)
    count = 0
    current = start_date
    while current <= end_day:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def round_half_up(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def ensure_indexes() -> None:
    db.employees.create_index([("emp_code", 1)], unique=True, name="ux_employees_emp_code")
    db.employees.create_index([("department", 1), ("emp_code", 1)], name="ix_employees_department_emp")
    db.employees.create_index([("department", 1), ("joined_on", 1)], name="ix_employees_department_joined")
    db.employees.create_index([("joined_on", 1), ("department", 1)], name="ix_employees_joined_department")
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True, name="ux_attendance_logs_emp_date")
    db.attendance_logs.create_index([("emp_code", 1), ("punch_in", 1)], name="ix_attendance_logs_emp_punchin")
    db.attendance_logs.create_index([("date", 1), ("emp_code", 1)], name="ix_attendance_logs_date_emp")
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)], name="ix_attendance_logs_date_desc_emp")
    db.attendance_logs.create_index([("status", 1), ("date", -1), ("emp_code", 1)], name="ix_attendance_logs_status_date_emp")
    db.attendance_logs.create_index([("status", 1)], name="ix_attendance_logs_status")


def attendance_list_filter(
    emp_code: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    status_value: Optional[str],
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        date_filter: dict[str, str] = {}
        if date_from is not None:
            date_filter["$gte"] = date_from
        if date_to is not None:
            date_filter["$lte"] = date_to
        query["date"] = date_filter
    if status_value is not None:
        query["status"] = status_value
    return query


def monthly_aggregation_pipeline(emp_code: str, month_start: date, month_end: date) -> list[dict[str, Any]]:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": month_start.isoformat(), "$lte": month_end.isoformat()}}},
        {"$group": {
            "_id": None,
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, "$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


def leaderboard_aggregation_pipeline(month_start: date, month_end: date, department: Optional[str]) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = [
        {"$match": {"date": {"$gte": month_start.isoformat(), "$lte": month_end.isoformat()}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$match": {"employee.joined_on": {"$lte": month_end.isoformat()}}},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$employee.name"},
            "department": {"$first": "$employee.department"},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
    ])
    return pipeline


def department_summary_attendance_pipeline(
    month_start: date,
    month_end: date,
    department: Optional[str],
) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = [
        {"$match": {"date": {"$gte": month_start.isoformat(), "$lte": month_end.isoformat()}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$match": {"employee.joined_on": {"$lte": month_end.isoformat()}}},
        {"$project": {
            "department": "$employee.department",
            "status": 1,
            "date": 1,
            "half_day": 1,
            "late_minutes": 1,
            "work_hours": 1,
        }},
    ]
    if department is not None:
        pipeline.append({"$match": {"department": department}})
    pipeline.append({"$sort": {"department": 1, "date": 1}})
    return pipeline


@app.on_event("startup")
def startup_event() -> None:
    ensure_indexes()


def parse_epoch_ms(value: Optional[int], *, allow_none: bool = True) -> Optional[datetime]:
    if value is None:
        if allow_none:
            return None
        raise ValueError("timestamp is required")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("timestamp must be an integer epoch millisecond value")
    if value < 100000000000 or value > 4102444800000:
        raise ValueError("timestamp is out of the accepted range")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def parse_time_string(value: str) -> time:
    hour_str, minute_str = value.split(":")
    return time(int(hour_str), int(minute_str))


def attendance_date_for_punch(employee: dict[str, Any], punch_dt_utc: datetime) -> str:
    punch_dt_utc = normalize_datetime(punch_dt_utc)
    ist_dt = punch_dt_utc.astimezone(IST)
    shift_start = parse_time_string(employee["shift_start"])
    shift_end = parse_time_string(employee["shift_end"])
    if shift_end <= shift_start:
        if ist_dt.time() < shift_end:
            return (ist_dt.date() - timedelta(days=1)).isoformat()
        return ist_dt.date().isoformat()
    return ist_dt.date().isoformat()


def shift_start_datetime(attendance_date: str, shift_start: str) -> datetime:
    return datetime.combine(date.fromisoformat(attendance_date), parse_time_string(shift_start), tzinfo=IST)


def shift_end_datetime(attendance_date: str, shift_end: str, overnight: bool) -> datetime:
    end_date = date.fromisoformat(attendance_date)
    if overnight:
        end_date += timedelta(days=1)
    return datetime.combine(end_date, parse_time_string(shift_end), tzinfo=IST)


def compute_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date: str) -> int:
    punch_in_utc = normalize_datetime(punch_in_utc)
    shift_start_dt = shift_start_datetime(attendance_date, shift_start)
    elapsed_seconds = (punch_in_utc.astimezone(IST) - shift_start_dt).total_seconds()
    if elapsed_seconds <= 600:
        return 0
    return int((elapsed_seconds // 60))


def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    punch_in_utc = normalize_datetime(punch_in_utc)
    punch_out_utc = normalize_datetime(punch_out_utc)
    total_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    hours = (Decimal(str(total_seconds)) / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(hours)


def compute_overtime_minutes(punch_out_utc: datetime, shift_end: str, attendance_date: str, shift_start: str) -> int:
    punch_out_utc = normalize_datetime(punch_out_utc)
    overnight = shift_end <= shift_start
    end_dt = shift_end_datetime(attendance_date, shift_end, overnight)
    diff_seconds = (punch_out_utc.astimezone(IST) - end_dt).total_seconds()
    if diff_seconds < 1800:
        return 0
    return int(diff_seconds // 60)


def compute_half_day(work_hours: Optional[float]) -> bool:
    if work_hours is None:
        return False
    rounded_hours = Decimal(str(work_hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return rounded_hours < Decimal("4.50")


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    emp_code: str
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120)
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: date

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must match EMP followed by 4 to 6 digits")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("email must be a valid email address")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("time must be in HH:MM format")
        return value

    @model_validator(mode="after")
    def validate_shift_window(self) -> "EmployeeCreate":
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


@app.get("/health")
def health() -> dict[str, str]:
    try:
        db.command("ping")
    except Exception as exc:  # pragma: no cover - exercised by tests with monkeypatch
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail="MongoDB unavailable") from exc
    return {"status": "ok"}


@app.post("/employees", status_code=http_status.HTTP_201_CREATED)
def create_employee(body: EmployeeCreate) -> dict[str, Any]:
    document = body.model_dump(exclude_none=True)
    document["joined_on"] = body.joined_on.isoformat()
    document["created_at"] = datetime.now(timezone.utc)

    try:
        result = db.employees.insert_one(document)
    except errors.DuplicateKeyError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="emp_code already exists") from exc

    created = db.employees.find_one({"_id": result.inserted_id})
    return serialize_employee(created)


@app.get("/employees")
def list_employees(
    department: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if department is not None:
        query["department"] = department

    total = db.employees.count_documents(query)
    items = list(
        db.employees.find(query, {"_id": 0}).sort("emp_code", 1).skip((page - 1) * page_size).limit(page_size)
    )
    for item in items:
        if "created_at" in item:
            item["created_at"] = as_epoch_ms(item["created_at"])

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="employee not found")

    month_start, month_end = month_range(month)
    pipeline = monthly_aggregation_pipeline(emp_code, month_start, month_end)
    agg_row = next(db.attendance_logs.aggregate(pipeline), {})

    records = list(
        db.attendance_logs.find(
            {"emp_code": emp_code, "date": {"$gte": month_start.isoformat(), "$lte": month_end.isoformat()}},
            {"_id": 0, "date": 1, "status": 1, "half_day": 1},
        )
    )

    present_days = Decimal("0")
    for record in records:
        if record.get("status") not in PRESENCE_STATUS:
            continue
        attendance_date = date.fromisoformat(record["date"])
        if attendance_date.weekday() >= 5:
            continue
        if record.get("half_day") is True:
            present_days += Decimal("0.5")
        else:
            present_days += Decimal("1")

    working_days = working_days_in_month(employee["joined_on"], month)
    leave_days = int(agg_row.get("leave_days", 0) or 0)
    late_count = int(agg_row.get("late_count", 0) or 0)
    total_late_minutes = int(agg_row.get("total_late_minutes", 0) or 0)
    total_overtime_minutes = int(agg_row.get("total_overtime_minutes", 0) or 0)

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            round_half_up((Decimal(str(present_days)) / Decimal(str(working_days)) * Decimal("100")), "0.0001")
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(present_days),
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    month_start, month_end = month_range(month)
    eligible_employee_filter: dict[str, Any] = {"joined_on": {"$lte": month_end.isoformat()}}
    if department is not None:
        eligible_employee_filter["department"] = department

    headcount_rows = list(
        db.employees.aggregate([
            {"$match": eligible_employee_filter},
            {"$group": {"_id": "$department", "headcount": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ])
    )
    headcount_by_department = {row["_id"]: row["headcount"] for row in headcount_rows}
    if not headcount_by_department:
        return {"month": month, "items": []}

    attendance_pipeline = department_summary_attendance_pipeline(month_start, month_end, department)
    attendance_rows = list(db.attendance_logs.aggregate(attendance_pipeline))

    department_metrics: dict[str, dict[str, Any]] = {}
    for dept in sorted(headcount_by_department):
        department_metrics[dept] = {
            "department": dept,
            "headcount": headcount_by_department[dept],
            "present_days": Decimal("0"),
            "work_hours_total": Decimal("0"),
            "work_hours_count": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "leave_count": 0,
            "on_duty_count": 0,
        }

    for record in attendance_rows:
        dept = record["department"]
        item = department_metrics.setdefault(
            dept,
            {
                "department": dept,
                "headcount": headcount_by_department.get(dept, 0),
                "present_days": Decimal("0"),
                "work_hours_total": Decimal("0"),
                "work_hours_count": 0,
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0,
            },
        )

        if record.get("status") == "LEAVE":
            item["leave_count"] += 1
        if record.get("status") == "ON_DUTY":
            item["on_duty_count"] += 1
        if record.get("late_minutes", 0) and record.get("late_minutes") > 0:
            item["late_count"] += 1
            item["total_late_minutes"] += int(record["late_minutes"])
        if record.get("status") in PRESENCE_STATUS and record.get("work_hours") is not None:
            item["work_hours_total"] += Decimal(str(record["work_hours"]))
            item["work_hours_count"] += 1
        if record.get("status") in PRESENCE_STATUS:
            attendance_date = date.fromisoformat(record["date"])
            if attendance_date.weekday() < 5:
                item["present_days"] += Decimal("0.5") if record.get("half_day") is True else Decimal("1")

    items = []
    for dept in sorted(department_metrics):
        if department_metrics[dept]["headcount"] == 0:
            continue
        avg_work_hours = None
        if department_metrics[dept]["work_hours_count"]:
            avg_work_hours = float(
                round_half_up(
                    department_metrics[dept]["work_hours_total"] / Decimal(str(department_metrics[dept]["work_hours_count"])),
                    "0.0001",
                )
            )
        items.append({
            "department": dept,
            "headcount": department_metrics[dept]["headcount"],
            "present_days": float(department_metrics[dept]["present_days"]),
            "avg_work_hours": avg_work_hours,
            "late_count": department_metrics[dept]["late_count"],
            "total_late_minutes": department_metrics[dept]["total_late_minutes"],
            "leave_count": department_metrics[dept]["leave_count"],
            "on_duty_count": department_metrics[dept]["on_duty_count"],
        })

    return {"month": month, "items": items}


def _sorted_dates_in_range(start_date: date, end_date: date) -> list[date]:
    return [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]


def department_trend_aggregation_pipeline(
    department: str,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    return [
        {"$match": {"date": {"$gte": from_date.isoformat(), "$lte": to_date.isoformat()}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {"$project": {
            "date": 1,
            "status": 1,
            "half_day": 1,
            "late_minutes": {"$ifNull": ["$late_minutes", 0]},
            "emp_code": 1,
        }},
        {"$sort": {"date": 1}},
    ]


def _trend_for_department_fallback(department: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
    dates = _sorted_dates_in_range(from_date, to_date)
    employee_rows = list(
        db.employees.find(
            {"department": department, "joined_on": {"$lte": to_date.isoformat()}},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )
    )
    headcount_by_date: dict[str, int] = {day.isoformat(): 0 for day in dates}
    for row in employee_rows:
        joined_on = date.fromisoformat(row["joined_on"])
        for day in dates:
            if joined_on <= day:
                headcount_by_date[day.isoformat()] += 1

    join_dates_by_emp = {
        row["emp_code"]: date.fromisoformat(row["joined_on"])
        for row in db.employees.find({"department": department, "joined_on": {"$lte": to_date.isoformat()}}, {"_id": 0, "emp_code": 1, "joined_on": 1})
    }
    attendance_rows = list(
        db.attendance_logs.aggregate(department_trend_aggregation_pipeline(department, from_date, to_date))
    )

    attendance_by_date: dict[str, dict[str, Any]] = {}
    for row in attendance_rows:
        joined_on = join_dates_by_emp.get(row["emp_code"])
        if joined_on is None or joined_on > date.fromisoformat(row["date"]):
            continue
        day_bucket = attendance_by_date.setdefault(row["date"], {"present_count": Decimal("0"), "late_count": 0})
        if row.get("status") in PRESENCE_STATUS:
            present_value = Decimal("0.5") if row.get("half_day") is True else Decimal("1")
            day_bucket["present_count"] += present_value
        if row.get("late_minutes", 0) and int(row["late_minutes"]) > 0:
            day_bucket["late_count"] += 1

    daily_rows: list[dict[str, Any]] = []
    for day in dates:
        iso = day.isoformat()
        is_working_day = day.weekday() < 5
        headcount = headcount_by_date.get(iso, 0)
        day_stats = attendance_by_date.get(iso, {"present_count": Decimal("0"), "late_count": 0})
        present_count = Decimal(str(day_stats["present_count"]))
        late_count = int(day_stats["late_count"])
        attendance_rate = None
        if is_working_day and headcount > 0:
            attendance_rate = float(
                round_half_up((present_count / Decimal(headcount)), "0.0001")
            )
        daily_rows.append({
            "date": iso,
            "is_working_day": is_working_day,
            "headcount": headcount,
            "present_count": float(present_count),
            "late_count": late_count,
            "attendance_rate": attendance_rate,
        })

    for index, row in enumerate(daily_rows):
        window_values = [
            prior_row["attendance_rate"]
            for prior_row in daily_rows[max(0, index - 6): index + 1]
            if prior_row["attendance_rate"] is not None
        ]
        if not window_values:
            row["moving_avg_7d"] = None
        else:
            average = sum(Decimal(str(value)) for value in window_values) / Decimal(len(window_values))
            row["moving_avg_7d"] = float(round_half_up(average, "0.0001"))

    return daily_rows


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
) -> dict[str, Any]:
    if to_date < from_date:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to must be greater than or equal to from")
    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date range must not exceed 92 days")

    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="department not found")

    try:
        rows = _trend_for_department_fallback(department, from_date, to_date)
        if not rows:
            return {"department": department, "items": []}
        return {"department": department, "items": rows}
    except Exception:
        raise


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = Query(default=None),
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    status_: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = Query(default=None, alias="status"),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    collection_name: str
    operation: dict[str, Any]

    if endpoint == "attendance_list":
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date_from must be <= date_to")
        collection_name = "attendance_logs"
        operation = {
            "find": collection_name,
            "filter": attendance_list_filter(
                emp_code,
                date_from.isoformat() if date_from else None,
                date_to.isoformat() if date_to else None,
                status_,
            ),
            "projection": {"_id": 0},
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
    elif endpoint == "employee_monthly":
        if emp_code is None or month is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="emp_code and month are required")
        month_start, month_end = month_range(month)
        collection_name = "attendance_logs"
        operation = {
            "aggregate": collection_name,
            "pipeline": monthly_aggregation_pipeline(emp_code, month_start, month_end),
            "cursor": {},
        }
    elif endpoint == "department_summary":
        if month is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="month is required")
        month_start, month_end = month_range(month)
        collection_name = "attendance_logs"
        operation = {
            "aggregate": collection_name,
            "pipeline": department_summary_attendance_pipeline(month_start, month_end, department),
            "cursor": {},
        }
    elif endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="month is required")
        month_start, month_end = month_range(month)
        collection_name = "attendance_logs"
        operation = {
            "aggregate": collection_name,
            "pipeline": leaderboard_aggregation_pipeline(month_start, month_end, department),
            "cursor": {},
        }
    else:
        if department is None or from_date is None or to_date is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="department, from, and to are required")
        if to_date < from_date:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to must be greater than or equal to from")
        if (to_date - from_date).days + 1 > 92:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date range must not exceed 92 days")
        if db.employees.find_one({"department": department}) is None:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="department not found")
        collection_name = "attendance_logs"
        operation = {
            "aggregate": collection_name,
            "pipeline": department_trend_aggregation_pipeline(department, from_date, to_date),
            "cursor": {},
        }

    try:
        explain_result = db.command("explain", operation, verbosity="executionStats")
    except PyMongoError as exc:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="MongoDB explain failed") from exc

    raw_explain = json_util.loads(json_util.dumps(explain_result))
    return {"endpoint": endpoint, "collection": collection_name, "explain": raw_explain}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    month_start, month_end = month_range(month)
    pipeline = leaderboard_aggregation_pipeline(month_start, month_end, department)
    rows = list(db.attendance_logs.aggregate(pipeline))
    if not rows:
        return {"month": month, "items": []}

    ranked_rows: list[dict[str, Any]] = []
    previous_total: Optional[int] = None
    previous_rank = 0
    for idx, row in enumerate(rows, start=1):
        if previous_total is None or row["total_late_minutes"] != previous_total:
            previous_rank = idx
            previous_total = row["total_late_minutes"]
        ranked_rows.append({
            "emp_code": row["_id"],
            "name": row["name"],
            "department": row["department"],
            "total_late_minutes": row["total_late_minutes"],
            "late_count": row["late_count"],
            "rank": previous_rank,
        })

    filtered_rows = [row for row in ranked_rows if row["rank"] <= limit]
    return {"month": month, "items": filtered_rows}


class PunchInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must match EMP followed by 4 to 6 digits")
        return value


class PunchOutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must match EMP followed by 4 to 6 digits")
        return value


class RegularizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


@app.post("/attendance/punch-in", status_code=http_status.HTTP_201_CREATED)
def punch_in(body: PunchInRequest) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="employee not found")

    punched_at_dt = parse_epoch_ms(body.punched_at) or datetime.now(timezone.utc)
    attendance_date = attendance_date_for_punch(employee, punched_at_dt)

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punched_at_dt,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punched_at_dt, employee["shift_start"], attendance_date),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        result = db.attendance_logs.insert_one(doc)
    except errors.DuplicateKeyError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="already punched in for this date") from exc

    created = db.attendance_logs.find_one({"_id": result.inserted_id})
    return serialize_attendance_record(created)


@app.post("/attendance/punch-out", status_code=http_status.HTTP_200_OK)
def punch_out(body: PunchOutRequest) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="employee not found")

    punched_out_dt = parse_epoch_ms(body.punched_at) or datetime.now(timezone.utc)
    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_out": None}, sort=[("punch_in", -1)])
    if record is None:
        latest_record = db.attendance_logs.find_one({"emp_code": body.emp_code}, sort=[("punch_in", -1)])
        if latest_record is not None:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="record already punched out")
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="punch-in not found")

    record["punch_in"] = normalize_datetime(record.get("punch_in"))
    if record["punch_in"] is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="punch-in not found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="record already punched out")

    if punched_out_dt <= record["punch_in"]:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out must be after punch_in")

    if punched_out_dt - record["punch_in"] > timedelta(hours=24):
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out exceeds 24 hours after punch_in")

    attendance_date = record["date"]
    shift_start = employee["shift_start"]
    shift_end = employee["shift_end"]
    work_hours = compute_work_hours(record["punch_in"], punched_out_dt)
    overtime_minutes = compute_overtime_minutes(punched_out_dt, shift_end, attendance_date, shift_start)
    half_day = compute_half_day(work_hours)

    update_filter = {"_id": record["_id"], "punch_out": None}
    update_doc = {"$set": {"punch_out": punched_out_dt, "work_hours": work_hours, "overtime_minutes": overtime_minutes, "half_day": half_day}}
    result = db.attendance_logs.update_one(update_filter, update_doc)
    if result.matched_count == 0:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="record already punched out")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance_record(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    status_: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date_from must be <= date_to")

    query = attendance_list_filter(emp_code, date_from, date_to, status_)

    total = db.attendance_logs.count_documents(query)
    items = list(
        db.attendance_logs.find(query, {"_id": 0}).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)
    )
    return {"items": [serialize_attendance_record(item) for item in items], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="attendance record not found")

    record["punch_in"] = normalize_datetime(record.get("punch_in"))
    record["punch_out"] = normalize_datetime(record.get("punch_out"))
    record["history"] = record.get("history", [])

    update_fields: dict[str, Any] = {}
    change_map: dict[str, dict[str, Any]] = {}

    if body.status is not None and body.status != record.get("status"):
        update_fields["status"] = body.status
        change_map["status"] = {"from": record.get("status"), "to": body.status}

    if body.punch_in is not None:
        punch_in_dt = parse_epoch_ms(body.punch_in)
        if punch_in_dt is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_in must be a valid epoch millisecond")
        if punch_in_dt != record.get("punch_in"):
            update_fields["punch_in"] = punch_in_dt
            change_map["punch_in"] = {"from": record.get("punch_in"), "to": punch_in_dt}

    if body.punch_out is not None:
        punch_out_dt = parse_epoch_ms(body.punch_out)
        if punch_out_dt is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out must be a valid epoch millisecond")
        if punch_out_dt != record.get("punch_out"):
            update_fields["punch_out"] = punch_out_dt
            change_map["punch_out"] = {"from": record.get("punch_out"), "to": punch_out_dt}

    if not update_fields:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="request changes nothing")

    final_status = update_fields.get("status", record.get("status"))
    final_punch_in = update_fields.get("punch_in", record.get("punch_in"))
    final_punch_out = update_fields.get("punch_out", record.get("punch_out"))

    if final_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="ABSENT and LEAVE records must not include punch times")
        final_punch_in = None
        final_punch_out = None

    if final_status in PRESENCE_STATUS and final_punch_in is None:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="presence statuses require punch_in")

    if final_punch_in is not None:
        expected_date = attendance_date_for_punch(employee, final_punch_in)
        if expected_date != date:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_in must match the attendance date")

    if final_punch_out is not None:
        if final_punch_in is None:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out requires punch_in")
        if final_punch_out <= final_punch_in:
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out must be after punch_in")
        if final_punch_out - final_punch_in > timedelta(hours=24):
            raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="punch_out must be within 24 hours of punch_in")

    if final_status in {"ABSENT", "LEAVE"}:
        final_work_hours = None
        final_late_minutes = 0
        final_overtime_minutes = 0
        final_half_day = False
    elif final_punch_in is not None:
        final_work_hours = compute_work_hours(final_punch_in, final_punch_out) if final_punch_out is not None else None
        final_late_minutes = compute_late_minutes(final_punch_in, employee["shift_start"], date)
        final_overtime_minutes = (
            compute_overtime_minutes(final_punch_out, employee["shift_end"], date, employee["shift_start"])
            if final_punch_out is not None
            else 0
        )
        final_half_day = compute_half_day(final_work_hours) if final_punch_out is not None else False
    else:
        final_work_hours = None
        final_late_minutes = 0
        final_overtime_minutes = 0
        final_half_day = False

    if final_punch_out is None and final_punch_in is not None:
        final_overtime_minutes = 0
        final_half_day = False

    if final_punch_out is not None and final_punch_in is not None:
        final_work_hours = compute_work_hours(final_punch_in, final_punch_out)
        final_late_minutes = compute_late_minutes(final_punch_in, employee["shift_start"], date)
        final_overtime_minutes = compute_overtime_minutes(final_punch_out, employee["shift_end"], date, employee["shift_start"])
        final_half_day = compute_half_day(final_work_hours)

    new_history = list(record.get("history", []))
    changes_payload: dict[str, dict[str, Any]] = {}
    for key, value in change_map.items():
        changes_payload[key] = {"from": value["from"], "to": value["to"]}

    final_change_keys = set(changes_payload)
    if not final_change_keys:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail="request changes nothing")

    if "punch_in" in changes_payload:
        changes_payload["punch_in"] = {"from": changes_payload["punch_in"]["from"], "to": final_punch_in}
    if "punch_out" in changes_payload:
        changes_payload["punch_out"] = {"from": changes_payload["punch_out"]["from"], "to": final_punch_out}
    if "status" in changes_payload:
        changes_payload["status"] = {"from": changes_payload["status"]["from"], "to": final_status}

    new_history.append({"at": datetime.now(timezone.utc), "by": body.regularized_by, "reason": body.reason, "changes": changes_payload})

    update_doc = {
        "$set": {
            "status": final_status,
            "punch_in": final_punch_in,
            "punch_out": final_punch_out,
            "work_hours": final_work_hours,
            "late_minutes": final_late_minutes,
            "overtime_minutes": final_overtime_minutes,
            "half_day": final_half_day,
            "history": new_history,
        }
    }

    result = db.attendance_logs.update_one({"_id": record["_id"], "history": record.get("history", [])}, update_doc)
    if result.matched_count == 0:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="attendance record was updated by another request")

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance_record(updated)
