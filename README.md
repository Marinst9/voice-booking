# Barber Shop Voice Booking API

![CI](https://github.com/YOUR-USERNAME/voice-booking/actions/workflows/ci.yml/badge.svg)
<!-- Replace YOUR-USERNAME above with your GitHub username after pushing. -->

The backend for an AI voice booking assistant for a barber shop. A voice agent calls these endpoints as
"tools" to check free times, book and cancel appointments. Every answer includes a sentence the agent
can read straight to the caller.

## Features

- **4 endpoints**: check availability, create booking, cancel booking, list bookings
- **Booking rules**: opening hours (Mon–Sat 09:00–18:00, Europe/Skopje), no double bookings, no past bookings
- **Caller-friendly errors**: *"Sorry, that time is already taken. The nearest free times are 13:30 and 14:30."*
  plus a machine-readable `error` code and `suggested_times`
- **PostgreSQL storage** with **Alembic** migrations; double bookings are also blocked **by the database itself**
- **Swappable storage**: in-memory (tests), PostgreSQL (real use), Google Calendar later, with no endpoint changes
- **API key security** (`X-API-Key` header) with an **Authorize** button in the interactive docs
- **Structured JSON logs** for every request, booking, cancellation and error, with a request id
- **Docker Compose**: one command starts the database, runs migrations and starts the API
- **106 automated tests**, run against both the in-memory calendar and a real PostgreSQL database
- **CI** on GitHub Actions: lint (ruff), formatting check, all tests, Docker build

## Architecture

```mermaid
flowchart LR
    caller([Caller]) --> agent[Voice AI agent]
    agent -- "HTTP + X-API-Key" --> api

    subgraph app ["FastAPI app (app/)"]
        api["main.py<br/>endpoints, API key, logging, errors"] --> svc["booking_service.py<br/>booking rules + spoken messages"]
        svc --> contract{{"CalendarBackend<br/>calendars/base.py"}}
        contract --> mem["InMemoryCalendar<br/>tests, quick runs"]
        contract --> pg["PostgresCalendar<br/>SQLAlchemy 2.0"]
        contract -.-> gcal["GoogleCalendar<br/>(future)"]
    end

    pg --> db[("PostgreSQL<br/>bookings table")]
    migrate["migrate container<br/>alembic upgrade head"] --> db
```

Each layer has one job and only talks to the layer below it:

| Layer | File(s) | Job |
|---|---|---|
| API | `app/main.py`, `app/models.py`, `app/api_docs.py` | HTTP, JSON shapes, `/docs`, error → JSON |
| Security | `app/security.py` | Checks the `X-API-Key` header |
| Rules | `app/booking_service.py` | Opening hours, overlaps, suggestions, spoken sentences |
| Storage contract | `app/calendars/base.py` | *What* any calendar must do |
| Storage | `app/calendars/memory.py`, `app/calendars/postgres.py` | *How* bookings are stored |
| Settings | `app/config.py` | Reads environment variables / `.env` |
| Logging | `app/logging_config.py` | JSON log lines with request ids |

## Quick start with Docker (recommended)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) running. In PowerShell, in the project folder:

1. **Create your settings file** (once):
   ```powershell
   Copy-Item .env.example .env
   ```
2. **Set an API key.** Generate a random one:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Open `.env` and replace the `API_KEY=` value with it.
3. **Start everything:**
   ```powershell
   docker compose up --build
   ```
   This starts PostgreSQL, waits until it's healthy, runs the migrations, then starts the API.
   Add `-d` to run it in the background. `docker compose logs -f api` shows the logs.
4. **Open http://127.0.0.1:8000/docs**, click **Authorize**, paste your API key, and try the endpoints.

Stop with `Ctrl+C` or `docker compose down`. Your bookings are kept in a Docker volume;
`docker compose down -v` deletes them.

## Running without Docker (for development)

Uses the in-memory calendar by default, so no database is needed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then set API_KEY in .env
$env:LOG_FORMAT = "text"         # optional: easier-to-read logs
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

To use PostgreSQL instead, start only the database with `docker compose up -d db`, set
`CALENDAR_BACKEND=postgres` in `.env`, and run the migrations once:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

## Configuration

All settings come from environment variables or `.env` (see `.env.example`). Only `API_KEY` is required.

| Variable | Default | Meaning |
|---|---|---|
| `API_KEY` | *(none, required)* | Secret clients send in `X-API-Key`. At least 16 characters. |
| `CALENDAR_BACKEND` | `memory` | `memory` or `postgres` (Docker Compose always uses `postgres`) |
| `DATABASE_URL` | – | e.g. `postgresql+psycopg://booking:booking@127.0.0.1:5432/booking` |
| `SHOP_TIMEZONE` | `Europe/Skopje` | Timezone for all times |
| `OPEN_DAYS` | `mon,tue,wed,thu,fri,sat` | Days the shop is open |
| `OPEN_TIME` / `CLOSE_TIME` | `09:00` / `18:00` | Opening hours |
| `SLOT_STEP_MINUTES` | `30` | Gap between offered start times |
| `SERVICES` | Haircut 30, Beard trim 20, Haircut + Beard 50 | JSON list of services |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` (servers) or `text` (local reading) |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` / `_PORT` | `booking` / `booking` / `booking` / `5432` | Used by Docker Compose |

Invalid values (an unknown timezone, closing before opening, ...) stop the app at startup with a clear message.

## Running the tests

```powershell
# Fast tests only (in-memory calendar, no database needed)
.\.venv\Scripts\python.exe -m pytest -m "not postgres"

# Everything, including the PostgreSQL tests
docker compose up -d db
$env:TEST_DATABASE_URL = "postgresql+psycopg://booking:booking@127.0.0.1:5432/booking_test"
.\.venv\Scripts\python.exe -m pytest

# Lint and formatting (the same checks as CI)
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format .
```

Tests that use a calendar run twice, shown as `test_name[memory]` and `test_name[postgres]`.
The PostgreSQL run uses a separate `booking_test` database, applies the real migrations, and empties
the table before each test. Without `TEST_DATABASE_URL`, those tests are skipped locally (on CI they fail instead).

What's covered: free slots, double and overlapping bookings, back-to-back bookings, closed days, outside hours,
past dates, cancellation, the API key, the docs, logging, settings validation, the database constraints,
and 8 simultaneous booking attempts for the same slot (exactly one wins).

## Example requests

In `/docs` you can try everything in the browser. From PowerShell:

```powershell
$api = "http://127.0.0.1:8000"
$headers = @{ "X-API-Key" = "your-api-key" }

# Free times
Invoke-RestMethod -Method Post "$api/check_availability" -Headers $headers -ContentType "application/json" `
  -Body '{"date": "2026-11-23", "service": "Haircut"}'

# Book
Invoke-RestMethod -Method Post "$api/create_booking" -Headers $headers -ContentType "application/json" `
  -Body '{"customer_name": "Marko", "phone": "+389 70 123 456", "service": "Haircut + Beard", "start_time": "2026-11-23T14:00"}'

# Cancel
Invoke-RestMethod -Method Post "$api/cancel_booking" -Headers $headers -ContentType "application/json" `
  -Body '{"phone": "+389 70 123 456", "start_time": "2026-11-23T14:00"}'

# List (add ?include_cancelled=true to see cancelled ones)
Invoke-RestMethod "$api/bookings" -Headers $headers
```

With curl (use `curl.exe` in Windows PowerShell, since plain `curl` is a different command there):

```bash
curl -X POST http://127.0.0.1:8000/create_booking \
  -H "X-API-Key: your-api-key" -H "Content-Type: application/json" \
  -d '{"customer_name": "Marko", "phone": "+389 70 123 456", "service": "Haircut", "start_time": "2026-11-23T14:00"}'
```

### Responses

Success:
```json
{
  "success": true,
  "message": "You're booked, Marko! Haircut on Monday 23 November at 14:00.",
  "booking": {
    "id": "5767523d205c4daf9a53fc8195b988a8", "customer_name": "Marko", "phone": "+38970123456",
    "service": "Haircut", "start_time": "2026-11-23T14:00:00+01:00", "end_time": "2026-11-23T14:30:00+01:00",
    "status": "confirmed", "created_at": "2026-11-21T22:33:58+01:00"
  }
}
```

Error (HTTP 409):
```json
{
  "success": false,
  "error": "time_taken",
  "message": "Sorry, that time is already taken. The nearest free times are 13:30 and 14:30.",
  "suggested_times": ["13:30", "14:30"]
}
```

| Status | `error` codes |
|---|---|
| 400 | `closed_day`, `outside_hours`, `date_in_past`, `time_in_past`, `unknown_service`, `invalid_phone`, `invalid_name` |
| 401 | `invalid_api_key` |
| 404 | `booking_not_found` |
| 409 | `time_taken` |
| 422 | `invalid_input` (missing field, bad date format, ...) |
| 500 | `internal_error` (details only in the logs) |

## Database and migrations

The `bookings` table has `status` (`confirmed` / `cancelled`) and `created_at`. Cancelling keeps the row and
marks it `cancelled`, so you keep a history. Times are stored as `timestamptz` (UTC inside the database) and
converted back to Skopje time by the app.

Double bookings are blocked by an **exclusion constraint**:

```sql
EXCLUDE USING gist (tstzrange(start_time, end_time, '[)') WITH &&) WHERE (status = 'confirmed')
```

In words: *no two confirmed bookings may have overlapping time ranges.* `[)` means the end time is not
included, so 13:30–14:00 and 14:00–14:30 are fine back to back.

Changing the schema later:
```powershell
# 1. Edit BookingRow in app/calendars/postgres.py
# 2. Generate a migration, then READ it before using it:
.\.venv\Scripts\alembic.exe revision --autogenerate -m "add notes column"
# 3. Apply it (Docker Compose also does this automatically on start):
.\.venv\Scripts\alembic.exe upgrade head
```

## Logging

Every log line is one JSON object, so log tools can search and filter it:

```json
{"time": "2026-11-21T21:33:58.473+00:00", "level": "INFO", "logger": "app.booking", "event": "booking_cancelled",
 "request_id": "30a959632f1c4dce916025472b71bc10", "booking_id": "5767...", "service": "haircut",
 "start": "2026-11-23T14:00:00+01:00", "phone": "***456"}
```

Events: `request_completed`, `booking_created`, `booking_cancelled`, `request_rejected`, `invalid_input`,
`auth_failed`, `unhandled_error`, `app_started`, `app_stopped`. All lines from one request share a `request_id`,
which is also returned in the `X-Request-ID` response header (or reused if the caller sends one).
Phone numbers are masked because they are personal data.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

1. **Lint**: `ruff check` and `ruff format --check`
2. **Tests**: all tests, against a real PostgreSQL started by GitHub
3. **Docker**: validates `docker-compose.yml` and builds the image

See the results in the **Actions** tab of your GitHub repository.

## Design decisions

- **30-minute slots.** Start times are offered every 30 minutes (09:00, 09:30, ...). This is easy to say out
  loud and easy for callers to remember. A shorter service (Beard trim, 20 min) still starts on the grid, which
  wastes up to 10 minutes but keeps the calendar tidy. Change it with `SLOT_STEP_MINUTES`.
  A booking for an off-grid time like 14:10 is still accepted if it fits.
- **Past bookings are rejected.** Past dates return `date_in_past`; earlier times today return `time_in_past`
  with the nearest free times. Availability for today only lists times that haven't passed yet.
- **Phone normalization.** Phones are stored as digits plus a leading `+` (e.g. `+38970123456`), so
  "+389 70 123 456" and "+38970-123-456" match when cancelling. Voice transcription often adds spaces or dashes.
  The phone + start time identifies a booking, so callers don't need to remember an id.
- **Skopje local time.** Callers think in local time, so the API takes and returns Europe/Skopje times. A time
  without a timezone (`2026-11-23T14:00`) is treated as Skopje time. Responses include the offset (`+01:00`, or
  `+02:00` in summer), so daylight saving time is handled correctly. The database stores UTC.
- **`suggested_times` in error responses.** When a time is taken or doesn't fit, the response lists the nearest
  free times, both in the spoken `message` and as a list. The agent can offer them right away without a second
  availability call or picking times out of a sentence.
- **Two layers against double bookings.** The service checks for overlaps under a lock (fast, gives a nice
  message), and PostgreSQL enforces it with a constraint (correct even with several servers). The in-memory
  calendar follows the same rule, so tests behave like production.
- **Cancel = mark as cancelled.** Rows are never deleted, which keeps a history; the constraint only applies to
  confirmed bookings, so a cancelled slot can be booked again.
- **API key instead of user logins.** The only client is a voice platform (machine to machine), so one secret key
  in a header is simple and enough. It's compared in constant time, and the server won't start without one.

## Project layout

```
app/
  main.py              endpoints, middleware (logging), error handlers, startup
  models.py            request/response JSON shapes
  api_docs.py          example responses for /docs
  booking_service.py   booking rules and spoken messages
  security.py          X-API-Key check
  errors.py            AppError -> friendly JSON
  config.py            settings from environment variables
  logging_config.py    structured JSON logging
  calendars/
    base.py            the calendar contract
    memory.py          in-memory calendar
    postgres.py        PostgreSQL calendar + table definition
migrations/            Alembic migrations
tests/                 pytest tests (conftest.py has the shared fixtures)
docker/postgres-init/  creates the booking_test database
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

## Adding Google Calendar later

1. Create `app/calendars/google.py` with `class GoogleCalendar(CalendarBackend)` implementing the methods in
   `app/calendars/base.py`. `add_booking` must raise `SlotTakenError` on overlap.
2. Add `"google"` to `CALENDAR_BACKEND` in `app/config.py` and a branch in `create_calendar()` in
   `app/calendars/__init__.py`.
3. Add it to the `calendar` fixture in `tests/conftest.py`, so the existing tests run against it too.

The endpoints and booking rules don't change.

## Troubleshooting

- **`API_KEY is not set`**: create `.env` from `.env.example` and set `API_KEY`.
- **Database connections are slow on Windows**: use `127.0.0.1` instead of `localhost` in database URLs.
  `localhost` tries IPv6 first, and each connection waits about 5 seconds.
- **Port already in use**: change `API_PORT` or `POSTGRES_PORT` in `.env`.
- **PostgreSQL tests can't find `booking_test`**: the database is created only the first time the db volume is
  made. Run `docker compose down -v` and `docker compose up -d db` (this deletes local bookings).
