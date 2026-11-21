# Barber Shop Voice Booking API

[![CI](https://github.com/Marinst9/voice-booking/actions/workflows/ci.yml/badge.svg)](https://github.com/Marinst9/voice-booking/actions/workflows/ci.yml)

The backend for an AI voice booking assistant for a barber shop. A voice agent calls these endpoints as
"tools" to check free times, book and cancel appointments. Every answer includes a sentence the agent
can read straight to the caller.

## Features

- **Vapi voice assistant integration**: a webhook adapter (`/vapi/events`, `/vapi/tools`), ready-to-paste tool
  definitions and a receptionist system prompt in `vapi/`
- **Call history**: every call's transcript, summary, duration, cost and ended reason is saved from Vapi's
  end-of-call report, linked to the booking made during the call
- **Admin dashboard** at `/admin`: today's and upcoming bookings with a Cancel button, call history with full
  transcripts, and this week's stats. Server-rendered (Jinja2), works on phones, protected by a login
- **4 endpoints**: check availability, create booking, cancel booking, list bookings
- **Booking rules**: opening hours (Mon–Sat 09:00–18:00, Europe/Skopje), no double bookings, no past bookings
- **Caller-friendly errors**: *"Sorry, that time is already taken. The nearest free times are 13:30 and 14:30."*
  plus a machine-readable `error` code and `suggested_times`
- **PostgreSQL storage** with **Alembic** migrations; double bookings are also blocked **by the database itself**
- **Swappable storage**: in-memory (tests), PostgreSQL (real use), Google Calendar later, with no endpoint changes
- **API key security** (`X-API-Key` header) with an **Authorize** button in the interactive docs
- **Structured JSON logs** for every request, booking, cancellation and error, with a request id
- **Docker Compose**: one command starts the database, runs migrations and starts the API
- **270+ automated tests**, run against both the in-memory storage and a real PostgreSQL database
- **CI** on GitHub Actions: lint (ruff), formatting check, all tests, Docker build

## Architecture

```mermaid
flowchart LR
    caller([Caller]) --> vapicloud["Vapi<br/>speech + LLM"]
    vapicloud -- "tool-calls + end-of-call-report<br/>Bearer VAPI_SECRET<br/>(via ngrok)" --> adapter
    clients([Other clients]) -- "HTTP + X-API-Key" --> api
    owner([Shop owner<br/>browser / phone]) -- "login + session cookie" --> admin

    subgraph app ["FastAPI app (app/)"]
        adapter["integrations/vapi.py<br/>Vapi format ⇄ services"] --> svc
        adapter --> calls
        api["main.py<br/>endpoints, API key, logging, errors"] --> svc["booking_service.py<br/>booking rules + spoken messages"]
        admin["admin/<br/>Jinja2 pages"] --> svc
        admin --> calls["call_service.py<br/>call history"]
        svc --> contract{{"CalendarBackend<br/>calendars/base.py"}}
        calls --> callstore{{"CallStore<br/>calls/base.py"}}
        contract --> mem["InMemoryCalendar<br/>tests, quick runs"]
        contract --> pg["PostgresCalendar<br/>SQLAlchemy 2.0"]
        contract -.-> gcal["GoogleCalendar<br/>(future)"]
        callstore --> pgcalls["PostgresCallStore"]
        callstore --> memcalls["InMemoryCallStore"]
    end

    pg --> db[("PostgreSQL<br/>bookings + calls tables")]
    pgcalls --> db
    migrate["migrate container<br/>alembic upgrade head"] --> db
```

Each layer has one job and only talks to the layer below it:

| Layer | File(s) | Job |
|---|---|---|
| API | `app/main.py`, `app/models.py`, `app/api_docs.py` | HTTP, JSON shapes, `/docs`, error → JSON |
| Admin dashboard | `app/admin/` | HTML pages (Jinja2 templates), login, CSRF protection |
| Integrations | `app/integrations/vapi.py` | Translates Vapi tool calls and end-of-call reports to the services and back |
| Security | `app/security.py` | Checks `X-API-Key` (REST) and the Vapi credential (`/vapi/...`) |
| Rules | `app/booking_service.py`, `app/call_service.py` | Booking rules and spoken sentences; linking calls to bookings |
| Storage contract | `app/calendars/base.py`, `app/calls/base.py` | *What* any calendar / call store must do |
| Storage | `app/calendars/`, `app/calls/` (memory + postgres each) | *How* bookings and calls are stored |
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
5. **Optional: the admin dashboard.** Set `ADMIN_PASSWORD` (and `SESSION_SECRET`) in `.env`, restart with
   `docker compose up -d`, and open http://127.0.0.1:8000/admin. See [Admin dashboard](#admin-dashboard).

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
| `VAPI_SECRET` | *(none)* | Secret Vapi sends to `/vapi/events` and `/vapi/tools`. Without it, they reject everything. |
| `ADMIN_USERNAME` | `admin` | Username for the dashboard at `/admin` |
| `ADMIN_PASSWORD` | *(none)* | Dashboard password, at least 10 characters. Empty = dashboard login turned off. |
| `SESSION_SECRET` | *(random at each start)* | Signs the login cookie, at least 32 characters. Set it, or everyone is logged out on restart. |
| `SESSION_COOKIE_SECURE` | `false` | `true` = the login cookie is only sent over HTTPS. Turn on when the site runs on HTTPS. |
| `SESSION_MAX_AGE_HOURS` | `8` | How long a dashboard login lasts |
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
An empty value (`VAPI_SECRET=`) counts as "not set". `CALENDAR_BACKEND` also decides where the call history is stored.

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
| 401 | `invalid_api_key`, `invalid_vapi_secret` (on `/vapi/...`) |
| 404 | `booking_not_found` |
| 409 | `time_taken` |
| 422 | `invalid_input` (missing field, bad date format, ...) |
| 500 | `internal_error` (details only in the logs) |

## Connecting a Vapi voice assistant

[Vapi](https://vapi.ai) handles the phone side (speech-to-text, the AI model, text-to-speech). When the assistant
needs the calendar, Vapi calls **`POST /vapi/tools`** on this backend. When a call ends, Vapi sends an
**end-of-call report** (transcript, summary, cost, ...) to **`POST /vapi/events`**, which saves it to the call
history. During development, **ngrok** gives your computer a public HTTPS address that Vapi can reach.

```
Caller ──> Vapi ──(during the call: tool-calls)────> https://xxxx.ngrok-free.app/vapi/tools  ──> ngrok ──> your PC :8000
               └──(after the call: end-of-call-report)──> https://xxxx.ngrok-free.app/vapi/events ─┘
```

Both URLs run the same code and accept every Vapi message type, so it doesn't matter if a setting points to the
"wrong" one. They're separate only because Vapi has two places to enter a URL: on each **tool** (step 4) and on
the **assistant** (step 7). The assistant's Server URL receives every event; Vapi sends tool calls to the tool's own
URL when it has one.

What's in `vapi/`:

| File | What it is |
|---|---|
| `vapi/tools/get_current_datetime.json` | Today's date and time in Skopje, plus the next 14 days with weekdays and opening hours |
| `vapi/tools/check_availability.json` | Free start times for a service on a day |
| `vapi/tools/create_booking.json` | Books an appointment |
| `vapi/tools/cancel_booking.json` | Cancels an appointment |
| `vapi/system-prompt.md` | The assistant's instructions: a friendly receptionist |

### Step 1: Set the Vapi secret and start the backend

1. Generate a secret:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Put it in `.env` as `VAPI_SECRET=...`. Keep this window open, you'll paste the same value into Vapi in step 3.
3. Start (or restart) the backend so it reads the new setting:
   ```powershell
   docker compose up -d --build
   ```

### Step 2: Start ngrok on port 8000

1. Create a free account at [ngrok.com](https://ngrok.com) and copy your **authtoken** from the ngrok dashboard
   (*Getting Started → Your Authtoken*).
2. Install ngrok, then **close and reopen PowerShell** so the `ngrok` command is found:
   ```powershell
   winget install ngrok.ngrok
   ```
3. Connect it to your account (once):
   ```powershell
   ngrok config add-authtoken YOUR-AUTHTOKEN
   ```
4. Start the tunnel and **leave this window open**:
   ```powershell
   ngrok http 8000
   ```
   Copy the `Forwarding` address, e.g. `https://abcd-1234.ngrok-free.app`. Your tool URL is that address plus
   `/vapi/tools`.
5. Check it: open `https://abcd-1234.ngrok-free.app/docs` in your browser. On the free plan ngrok first shows a
   warning page; click **Visit Site**. Vapi's requests skip that page.

**Tip:** on the free plan, the address changes every time you restart ngrok, and then you'd have to update all
four tools. Instead, claim your free static domain in the ngrok dashboard (*Domains*) and always start with:
```powershell
ngrok http --url=your-name.ngrok-free.app 8000
```

**Tip:** open **http://127.0.0.1:4040** while ngrok runs. It shows every request Vapi sends and your server's
answer, which is the easiest way to debug.

### Step 3: Create the credential in Vapi

This is how Vapi proves to your backend that a request really comes from your assistant.

1. Sign up at [dashboard.vapi.ai](https://dashboard.vapi.ai).
2. Find **Custom Credentials** (under *Integrations → Server Configuration*; Vapi moves menu items around
   sometimes) and create a new one:
   - **Type:** Bearer Token
   - **Name:** e.g. `Barber backend`
   - **Token:** the `VAPI_SECRET` value from your `.env`
   - Keep the header as `Authorization` with the **Bearer prefix** on (the default).
3. Save it.

The backend also accepts the legacy style (header name `X-Vapi-Secret`, Bearer prefix off), if you prefer that.

### Step 4: Add the four tools

In the dashboard, go to **Tools → Create Tool** and choose a **Custom Tool / Function**. Create one tool per file in
`vapi/tools/`, copying the values from the JSON:

| Dashboard field | Take it from the JSON |
|---|---|
| Tool name | `function.name` (must match exactly, e.g. `check_availability`) |
| Description | `function.description` |
| Parameters | `function.parameters` (paste the JSON object, or add each property with the same name, type, description and enum) |
| Server URL | `https://YOUR-NGROK-DOMAIN/vapi/tools` (replace the placeholder with your ngrok address) |
| Credential | the credential from step 3 |
| Timeout | 20 seconds |
| Messages → Request Start | the `content` of the `request-start` message (leave empty for `get_current_datetime`) |

<details>
<summary>Optional: create the tools with the Vapi API instead of clicking</summary>

This posts the JSON files directly. You need your **private API key** (dashboard → *API Keys*) and the
**credential id** (shown on the credential from step 3). This snippet hasn't been run against a live Vapi account,
so check the tools in the dashboard afterwards.

```powershell
$vapiKey      = "your-vapi-private-api-key"
$serverUrl    = "https://your-name.ngrok-free.app/vapi/tools"
$credentialId = "your-credential-id"

Get-ChildItem vapi\tools\*.json | ForEach-Object {
    $tool = Get-Content $_.FullName -Raw | ConvertFrom-Json
    $tool.server.url = $serverUrl
    $tool.server | Add-Member -NotePropertyName credentialId -NotePropertyValue $credentialId
    Invoke-RestMethod -Method Post "https://api.vapi.ai/tool" -ContentType "application/json" `
        -Headers @{ Authorization = "Bearer $vapiKey" } -Body ($tool | ConvertTo-Json -Depth 20)
}
```
</details>

### Step 5: Create the assistant

1. Go to **Assistants → Create Assistant** and start from the **Blank** template. Name it e.g. `Barber receptionist`.
2. **Model:** choose a model that's good at tool use (for example a current GPT, Claude or Gemini model).
3. **First message:**
   `Hi, thanks for calling! I can help you book or cancel an appointment. What can I do for you?`
4. **System prompt:** paste the whole content of `vapi/system-prompt.md`. Add your shop's name in the first line
   if you like.
5. **Tools:** add all four tools from step 4.
6. Leave voice and transcriber on the defaults for a first test, then **Publish / Save**.

### Step 6: Make a test call from the browser

1. Make sure three things are running: the backend (`docker compose ps`), ngrok, and nothing else on port 8000.
2. In a second PowerShell window, watch the logs:
   ```powershell
   docker compose logs -f api
   ```
3. In the Vapi dashboard, open your assistant and click **Talk to Assistant** (allow microphone access).
4. Try a conversation like this:
   - "Hi, I'd like a haircut tomorrow afternoon." (Tomorrow must be an open day. Try "on Monday" if tomorrow is Sunday.)
   - Pick one of the offered times, give a name and a phone number, and say "yes" to the confirmation.
   - Call again, ask for the same time, and listen to it offer `suggested_times`.
   - "I'd like to cancel my appointment."
5. Check the result: http://127.0.0.1:8000/docs → **Authorize** with your `API_KEY` → `GET /bookings`.

In the logs you'll see one `vapi_tool_call` line per tool call, with the tool name, the outcome and Vapi's call id.

### Step 7: Turn on the call history (end-of-call report)

After every call Vapi can send an **end-of-call report** with the transcript, Vapi's AI summary, the cost, the
start and end time and why the call ended. It's sent to the **assistant's** Server URL (the tools' URL is only
used for tool calls), so set that once:

1. In the Vapi dashboard, open **Assistants**, select your assistant, and go to the **Advanced** tab.
2. Under **Webhook Server** (it may be called *Server URL* or *Server* in your version of the dashboard):
   - **Server URL:** `https://YOUR-NGROK-DOMAIN/vapi/events`
   - **Timeout:** the default (20 seconds) is fine
   - **Authorization:** select the same **Bearer Token credential** as in step 3. Without it, the report is
     rejected with `401` and nothing is saved.
3. Under **Server Messages** (same tab), make sure **`end-of-call-report`** is selected. It's on by default, but
   check it if you changed the list. The backend acknowledges and ignores the other message types, so leaving
   them on does no harm.
4. **Publish** the assistant.
5. Make a test call (step 6) and hang up. Within a few seconds the call appears at
   http://127.0.0.1:8000/admin/calls, and the logs show a `call_recorded` line.

How a call is linked to its booking: when the assistant books during a call, `create_booking` stores Vapi's call id
on the booking. When the report arrives after the call, the call is linked to the latest booking with that id.
So "% of calls that ended in a booking" counts calls where a booking was actually made (even if it was cancelled
later), not calls where the caller only asked about times.

Not saved: the recording URL and the per-message data. If Vapi sends the same report twice, the call is
updated rather than duplicated.

### When something doesn't work

Open http://127.0.0.1:4040 (ngrok's inspector) and look at the request Vapi sent:

| You see | Likely cause |
|---|---|
| No request at all | Wrong Server URL on the tool, or ngrok isn't running / its address changed |
| `401` with `invalid_vapi_secret` | The credential token doesn't match `VAPI_SECRET`, no credential is selected on the tool, or the backend wasn't restarted after editing `.env` |
| `200` with `"error": "Unknown tool ..."` | The tool name in Vapi doesn't exactly match the file name (e.g. `check_availability`) |
| `502` / `ERR_NGROK_8012` | ngrok works but the backend isn't running on port 8000 |
| The assistant invents times | Check the tools are attached to the assistant and the system prompt is pasted |
| Calls don't appear in `/admin/calls` | The assistant has no Server URL (step 7), it has no credential (`401` on `/vapi/events` in the inspector), or `end-of-call-report` isn't in its Server Messages |
| Call shows "No booking" although one was made | The booking was made through the REST API or `/docs`, not by the assistant during that call |

Vapi only sends your credential for **saved** tools and assistants (created in the dashboard or with the API),
not for assistants passed inline when starting a call. That's another possible cause of a `401`.

### How the adapter answers

Vapi requires HTTP 200 with `{"results": [{"toolCallId": ..., "name": ..., "result": "<single-line string>"}]}`.
`result` is the same JSON the REST endpoints return, compacted to one line, so the assistant gets `success`,
`message`, `free_times`, `suggested_times`, and so on. Booking outcomes like "time taken" are normal results the
assistant should read to the caller. Only real failures (unknown tool, server crash) use Vapi's `error` field.
Every other message type (end-of-call-report, status updates, ...) gets `200` with `{}`, since Vapi expects no answer.

## Admin dashboard

A small website for the shop owner at **http://127.0.0.1:8000/admin**. It's plain HTML rendered by the server
(Jinja2 templates in `app/admin/templates/`), with no JavaScript framework and no separate frontend to build.
It works on a phone: tables turn into cards on small screens, and it follows the phone's light or dark mode.

| Page | What it shows |
|---|---|
| `/admin` | This week's stats, today's bookings and all upcoming bookings, each with a **Cancel** button |
| `/admin/calls` | Call history, newest first (25 per page): time, caller, duration, why it ended, booked or not |
| `/admin/calls/{id}` | One call: the full transcript as a chat, Vapi's summary, cost, and the linked booking |

The stats, for this week (Monday to Sunday, Skopje time):
- **Bookings this week:** confirmed appointments that take place this week
- **Calls this week:** calls that started this week
- **Calls that ended in a booking:** the share of this week's calls during which the assistant made a booking

### Turning it on

1. Add to `.env` (generate the secret like the API key):
   ```
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=a-long-password-you-choose
   SESSION_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
   ```
2. Restart: `docker compose up -d` (or restart uvicorn).
3. Open http://127.0.0.1:8000/admin and log in.

### Screenshots

<!-- Replace these placeholders with real screenshots: save them as PNG files in docs/screenshots/
     (e.g. dashboard.png) and change the links below from .svg to .png. -->

| Bookings and stats | Call history | Call detail |
|---|---|---|
| ![Bookings and stats](docs/screenshots/dashboard.svg) | ![Call history](docs/screenshots/calls.svg) | ![Call detail with transcript](docs/screenshots/call-detail.svg) |

### How the login works

- The username and password are compared in constant time, and a wrong login only says "Wrong username or
  password." (not which one was wrong).
- After logging in, the browser gets a **session cookie** (`admin_session`), signed with `SESSION_SECRET`.
  The signature means nobody can forge or change it. It holds only the username and a form token, no password.
  It's `HttpOnly`, `SameSite=Lax`, expires after `SESSION_MAX_AGE_HOURS`, and is cleared by **Log out**.
- Every form (login, cancel, log out) carries a random **CSRF token** that must match the session, so another
  website can't make your browser cancel bookings.
- Changing `ADMIN_USERNAME` or removing `ADMIN_PASSWORD` logs out existing sessions. To log everyone out after
  a **password** change, also change `SESSION_SECRET`.
- Pages are sent with `Cache-Control: no-store` (customer data isn't kept in the browser cache) and
  `X-Frame-Options: DENY`. Everything a caller said is HTML-escaped, so a transcript can't inject code.
- The dashboard has its own login, separate from `API_KEY`: the API key doesn't open it and a dashboard login
  doesn't open the REST API.

**Before putting it on the internet:** serve it over HTTPS and set `SESSION_COOKIE_SECURE=true`. There is no
limit on login attempts yet, so use a long, random password. The dashboard is also reachable through your ngrok
address while ngrok runs.

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

Bookings also have `call_id`: the Vapi call that made them (empty for bookings made through the REST API).

The `calls` table (migration `0002`) holds the call history, one row per Vapi call: `id` (Vapi's call id),
`started_at`, `ended_at`, `duration_seconds`, `ended_reason`, `transcript`, `summary`, `cost` (USD, stored as
`numeric` so money has no rounding errors), `customer_number`, `booking_id` and `created_at`. Saving uses
`INSERT ... ON CONFLICT (id) DO UPDATE`, so a repeated report updates the row instead of adding a second one.
`booking_id` is plain text rather than a foreign key, because the calendar might later live somewhere else
(Google Calendar).

Changing the schema later:
```powershell
# 1. Edit BookingRow in app/calendars/postgres.py (or CallRow in app/calls/postgres.py)
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

Events: `request_completed`, `booking_created`, `booking_cancelled` (with `cancelled_by`: `caller` or `admin`),
`request_rejected`, `invalid_input`, `auth_failed`, `unhandled_error`, `app_started`, `app_stopped`,
`vapi_tool_call`, `call_recorded`, `vapi_report_without_call_id`, `admin_login`, `admin_login_failed`,
`admin_logout`, `csrf_rejected`, `session_secret_missing`. All lines from one request share a `request_id`,
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
- **Vapi as a thin adapter.** `app/integrations/vapi.py` only translates formats and calls the same
  `BookingService`, so the voice assistant and the REST API can never follow different rules. It has its own
  secret (`VAPI_SECRET`), so it can be changed without touching `API_KEY`.
- **`get_current_datetime` returns a 14-day list.** Language models are unreliable at calendar maths. Instead of
  having the model work out "next Monday", it looks the date up in `upcoming_days`, which already says whether
  the shop is open that day.
- **API key instead of user logins.** The only client is a voice platform (machine to machine), so one secret key
  in a header is simple and enough. It's compared in constant time, and the server won't start without one.
- **One Vapi handler for every message type.** Vapi sends all events of an assistant to one Server URL and tells
  them apart by `message.type`, so `/vapi/events` dispatches on the type instead of having one URL per event.
  `/vapi/tools` is the same handler, so the tool URLs from before keep working.
- **The report is read defensively.** Several fields exist in more than one place (`startedAt` and
  `call.startedAt`; `artifact.transcript` and the older top-level `transcript`), so the adapter checks each
  place. A field it can't read is stored as empty rather than rejecting the whole report: losing one call's
  cost is better than losing the call. Vapi doesn't document a duration field, so the duration is
  calculated from the start and end times (a `durationSeconds` field is used if one is sent).
- **The booking stores the call id, not the other way round.** The booking happens *during* the call, but the
  call row only exists once the report arrives *after* it. Writing the call id on the booking right away and
  linking them when the report comes in needs no "unfinished call" rows.
- **Server-rendered dashboard.** Jinja2 HTML pages need no build step, no separate frontend and no API for the
  browser, and they work without JavaScript (only the "Are you sure?" question on Cancel uses it). That's enough
  for a few pages used by one shop owner.
- **Session cookie for the dashboard, API key for machines.** People log in once with a password; programs send a
  key with every request. Mixing them would mean storing the API key in the browser.

## Project layout

```
app/
  main.py              endpoints, middleware (logging), error handlers, startup
  models.py            request/response JSON shapes
  api_docs.py          example responses for /docs
  booking_service.py   booking rules and spoken messages
  call_service.py      call history: saving calls, linking them to bookings
  security.py          X-API-Key and Vapi credential checks
  dependencies.py      shared FastAPI dependencies
  errors.py            AppError -> friendly JSON
  database.py          shared SQLAlchemy Base (all tables)
  integrations/
    vapi.py            Vapi webhook adapter (tool calls + end-of-call reports)
  admin/
    routes.py          the dashboard pages
    auth.py            login, session, CSRF tokens
    views.py           formatting helpers (durations, transcript lines, ...)
    templates/         Jinja2 HTML templates (base.html has all the CSS)
  config.py            settings from environment variables
  logging_config.py    structured JSON logging
  calendars/
    base.py            the calendar contract
    memory.py          in-memory calendar
    postgres.py        PostgreSQL calendar + table definition
  calls/
    base.py            the call store contract
    memory.py          in-memory call store
    postgres.py        PostgreSQL call store + table definition
vapi/                  Vapi tool definitions (tools/*.json) and the assistant's system prompt
docs/screenshots/      dashboard screenshots for this README (placeholders for now)
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
- **"The admin login is turned off"**: set `ADMIN_PASSWORD` in `.env` (at least 10 characters) and restart.
- **Logged out of the dashboard after every restart**: set `SESSION_SECRET` in `.env`.
- **"This form has expired"** on the dashboard: the page was open from before a restart or logout. Reload it.
- **Dashboard login works on http://127.0.0.1 but not through ngrok** (or the other way round): the cookie belongs
  to one address. Log in again on the other one. With `SESSION_COOKIE_SECURE=true` it only works over HTTPS.
- **PostgreSQL tests can't find `booking_test`**: the database is created only the first time the db volume is
  made. Run `docker compose down -v` and `docker compose up -d db` (this deletes local bookings).
