# Employee Attendance & Analytics API

FastAPI and PyMongo implementation of the HROne employee attendance assignment. The API contract and business rules are
defined in [openapi.yaml](./openapi.yaml); MongoDB document shapes are defined in [DATA_MODEL.md](./DATA_MODEL.md).

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer (the assignment targets MongoDB 7.0)

## Local setup

From this directory, create and activate a virtual environment, then install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, use `python3.11 -m venv .venv` and `source .venv/bin/activate`.

Set the connection configuration in the process environment. For example, in PowerShell:

Start the API:

```powershell
python -m uvicorn app.main:app --port 8000
```

Check readiness at `http://localhost:8000/health`. The API creates its indexes at startup. To load the sample documents,
run `python sample_seed.py` with the same MongoDB configuration.

## Endpoints

- `GET /health`
- `POST, GET /employees`
- `POST /attendance/punch-in`
- `POST /attendance/punch-out`
- `GET /attendance`
- `PATCH /attendance/{emp_code}/{date}`
- `GET /analytics/employees/{emp_code}/monthly`
- `GET /analytics/departments/summary`
- `GET /analytics/leaderboard/late`
- `GET /analytics/departments/{department}/trend`
- `GET /admin/explain/{endpoint}`

Instants use epoch milliseconds at the API boundary and BSON UTC datetimes in MongoDB. Attendance dates and shift times
are interpreted in IST. The implementation includes overnight shift dates, late/overtime/work-hours/half-day rules,
database-enforced uniqueness for employee and attendance keys, atomic punch-out, and regularization history. Analytics
uses MongoDB aggregation for metric accumulation, competition ranking, and department trend calendar generation and
moving averages. Stored derived attendance fields are authoritative; weekday calculations exclude weekends where
required, and department headcount includes eligible employees without logs. The pipelines use operators available in
MongoDB 6.0, including `$dateAdd`, `$dateDiff`, `$rank`, and `$setWindowFields` (introduced in MongoDB 5.0).

## Tests

Run the automated suite and compile check:

```powershell
python -m pytest -q tests/
python -m compileall -q app tests
```

Employee and attendance endpoint tests use `mongomock`. Analytics and explain integration tests connect to
`mongodb://localhost:27017`, use uniquely named temporary databases, and skip when MongoDB is unavailable. The current
validation environment ran them on MongoDB 8.2.1; MongoDB 6.0 has not been separately tested.

## Limitations

The assignment's large-dataset behavior has not been verified against its approximately 100,000 attendance records.
Explain-plan checks and aggregation integration tests used small fixtures. No performance or latency claim is made for
the target dataset or production workloads.
