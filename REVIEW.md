# Starter-code review

The original starter was replaced during the phased implementation, so its exact line numbers are no longer present in
the checkout. Locations below name the original function or endpoint; no line numbers are inferred.

## Confirmed defects in the original starter

| # | Original code location | Issue and example symptom | Fix implemented |
|---|---|---|---|
| 1 | `health` | Returned 200 without checking MongoDB; readiness stayed green during a database outage. | Pings MongoDB and returns 503 on failure. |
| 2 | Startup / `ensure_indexes` | Did not create indexes; unindexed scans and duplicate natural keys were possible. | Creates named unique and supporting indexes at startup; repeated creation is safe. |
| 3 | `employees.emp_code` persistence | No database uniqueness guarantee for concurrent employee creates. | Adds unique `employees.emp_code`; duplicate-key errors map to 409. |
| 4 | Attendance persistence | No database uniqueness guarantee for one employee/date record. | Adds unique `attendance_logs(emp_code, date)`; duplicate punch-in is rejected by the index. |
| 5 | `list_employees` pagination | The page offset was calculated incorrectly; a page could skip or misplace results. | Uses `skip((page - 1) * page_size)` with a bounded database query. |
| 6 | `list_employees` total | Count did not consistently apply the department filter, so `total` could disagree with `items`. | Counts the same filter used by the database page query. |
| 7 | `create_employee` / employee validation | Documented employee code, email, shift, and body constraints were not enforced consistently. Invalid values could be stored. | Pydantic validators enforce the documented constraints and reject equal shift start/end. |
| 8 | Employee response serialization | MongoDB `_id`/datetime representation did not match the API; `created_at` must be epoch milliseconds and `_id` is private. | Serializers omit `_id` and convert instant values to epoch milliseconds. |
| 9 | `attendance_date_for_punch`, `compute_late_minutes` | Did not fully implement IST attendance dates/overnight shifts and strict R2 grace/floor behavior. | Uses IST, assigns overnight early punches to the previous shift date, truncates instants to seconds, and applies R2. |
| 10 | `compute_work_hours` | Work hours did not reliably use seconds and decimal half-up rounding. | Computes duration from truncated instants and rounds to two decimals with `ROUND_HALF_UP`. |
| 11 | `compute_overtime_minutes` / half-day calculation | Overtime threshold/overnight end handling and the rounded 4.50-hour half-day boundary were incorrect or incomplete. | Implements R3/R5 thresholds and overnight shift-end dates. |
| 12 | `punch_in` | Did not enforce all status/date/input rules or database-level duplicate handling. | Validates body/status and employee, computes the IST date, and relies on the unique index for conflicts. |
| 13 | `list_attendance` | Did not apply the contract's complete filters, stable sort, count, and page behavior in MongoDB. | Builds a database filter, counts it, and sorts/skips/limits in MongoDB. |
| 14 | `punch_out` | The starter lacked the required close behavior; concurrent requests could both appear successful. | Selects an eligible punch-in and atomically updates only a record with `punch_out: null`; a losing update returns 409. |
| 15 | `regularize_attendance` / history | The correction endpoint and audit trail behavior were missing; concurrent updates could lose history. | Recomputes derived fields, appends changed-field history, supports omitted seeded history, and returns 409 on stale history. |
| 16 | Analytics routes | Monthly, department summary, leaderboard, and trend routes were missing. | Added the routes and tests; metrics, ranking, and trend window calculations now run in MongoDB aggregation. |
| 17 | Admin explain route | The endpoint was missing, preventing inspection of MongoDB plans. | Added actual `executionStats` explain calls for the five supported operations. |

## Current verification gaps and risks

- Analytics metric accumulation, leaderboard competition ranking, trend calendar generation, and moving averages run in MongoDB aggregation pipelines. Trend dates are generated once with `$range`/`$dateAdd`, combined with the date-filtered attendance stream through `$unionWith`, then ranked/windowed in MongoDB.
- The pipelines use operators available in MongoDB 6.0 (`$dateAdd`, `$dateDiff`, `$rank`, and `$setWindowFields` were introduced in MongoDB 5.0), but direct integration verification ran on MongoDB 8.2.1; MongoDB 6.0 compatibility remains unverified in a server test.
- Analytics and explain tests use small, isolated live MongoDB fixtures. The trend pipeline groups department join dates into one intermediate array; its memory use, as well as performance and scan behavior on the assignment's approximately 100,000 attendance records, remains unverified.

## Checked, not classified as defects

- `history` and `half_day` may be omitted in seeded records; the data model explicitly defines defaults of empty history and false.
- A weekday with no attendance document is not equivalent to an `ABSENT` record; trend/summary behavior must preserve that distinction.
