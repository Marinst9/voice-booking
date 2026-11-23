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
- **Production-ready**: `ENVIRONMENT=production` mode, rate limiting (API, Vapi, admin login lockout), `/health`
  check, `/docs` off by default, HTTPS-only cookies, and two step-by-step deployments that deploy `main`
  automatically when CI passes: [free on Render + Neon](#free-deployment-render--neon) or
  [paid on Railway](#deploying-to-railway)
- **330+ automated tests**, run against both the in-memory storage and a real PostgreSQL database
- **CI** on GitHub Actions: lint (ruff), formatting check, all tests, Docker build + production smoke test

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
| `SESSION_COOKIE_SECURE` | *(false in development, true in production)* | `true` = the login cookie is only sent over HTTPS |
| `SESSION_MAX_AGE_HOURS` | `8` | How long a dashboard login lasts |
| `ENVIRONMENT` | `development` | `production` = refuse unsafe settings, `/docs` off, secure cookies, HSTS |
| `DOCS_ENABLED` | *(on in development, off in production)* | Show `/docs`, `/redoc` and `/openapi.json` |
| `CLIENT_IP_HEADER` | *(none)* | Header with the real client IP behind a trusted proxy (Render: `CF-Connecting-IP`, Railway: `X-Real-IP`). Leave empty locally: a client could fake it |
| `RATE_LIMIT_API_PER_MINUTE` | `60` | Requests per minute per IP to the REST endpoints (`0` = no limit) |
| `RATE_LIMIT_VAPI_PER_MINUTE` | `300` | Requests per minute per IP to `/vapi/...` |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCKOUT_MINUTES` | `5` / `15` | Wrong admin logins allowed per IP, and how long that IP is then locked out |
| `PORT` | `8000` | Port the Docker image listens on (Railway: `8080`) |
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

**Before putting it on the internet:** use `ENVIRONMENT=production` (see [Deploying to Railway](#deploying-to-railway)):
it makes the cookie HTTPS-only and refuses to start without `SESSION_SECRET`. After `LOGIN_MAX_FAILURES` (5) wrong
passwords from one IP address, logins from that address are blocked for `LOGIN_LOCKOUT_MINUTES` (15), even with
the right password. The dashboard is also reachable through your ngrok address while ngrok runs.

## Hosting options

| | [Free: Render + Neon](#free-deployment-render--neon) | [Paid: Railway](#deploying-to-railway) |
|---|---|---|
| Cost | $0 | About $5/month (Hobby) |
| Always on | Kept awake by a ping every 10 minutes | Yes |
| Restarts | Render may restart free services at any time (about 1 minute offline) | Only when you deploy |
| Database | Neon free: 1 GB, sleeps when idle (wakes in under a second) | Railway Postgres in the same project |
| Migrations | At app startup | Pre-deploy command |
| Good for | Portfolio, demos, test calls | A real shop's phone line |

## Free deployment: Render + Neon

The app runs on a [Render](https://render.com) **free web service**, the database on [Neon](https://neon.com)'s
**free Postgres**, and [cron-job.org](https://cron-job.org) pings the app every 10 minutes so it doesn't fall asleep.

```
Caller ─> Vapi ─> https://voice-booking.onrender.com/vapi/...   ─┐
You ─> https://voice-booking.onrender.com/admin                    ├─> Render free web service (Frankfurt)
cron-job.org ─(every 10 min)─> /health/live                       ─┘        │
                                                                           └─> Neon Postgres (Frankfurt)
git push ─> GitHub Actions (CI) ─ passes ─> Render deploys main (migrations run at startup)
```

### Limits you should know about

- **Render free service:**
  - It sleeps after **15 minutes without requests**, and waking takes **about a minute**. That's longer than
    Vapi's 20-second tool timeout, which is why the ping keeps it awake.
  - There are **750 free hours per month per workspace**. Awake 24/7 uses up to 744 (31 days), so it fits,
    but only for **one** free service. A second free web service in the same Render workspace would use up the
    hours, and Render then suspends **all** free services until the next month.
  - Render may **restart** it at any time, and each deploy restarts it. For about a minute, calls get failed
    tool calls.
  - It has 0.1 CPU and 512 MB RAM. It's enough for this app, but slower than your laptop.
  - Render's docs say free instances are not meant for production.
- **Neon free database:**
  - It sleeps after 5 idle minutes and wakes in a few hundred milliseconds, so the first tool call after a
    quiet period is a little slower.
  - The limits per month are **100 compute hours** (400 hours at the smallest size) and 5 GB of data
    transfer. At either limit the database is **suspended until next month** (no data is lost).
  - That's why the ping and Render's health check use **`/health/live`, which never touches the database**:
    Neon wakes only for real calls and dashboard visits.
  - Storage is 1 GB, which is a lot for bookings and transcripts. Restores only go back 6 hours, so make your
    own backups (below).
- **The ping:** if cron-job.org misses a ping, Render can put the app to sleep, and the next call's first
  tool call then fails while it wakes up.
- **All free tiers can change.** Check the limits below now and then.

### Step 1: Create the Neon database

1. Go to https://neon.com → **Sign up** (GitHub login works). The free plan needs no credit card.
2. Create a project:
   - **Project name:** `voice-booking`
   - **Postgres version:** the default
   - **Region:** **AWS Europe Central 1 (Frankfurt)**, the same region as the Render service. Every tool call
     makes a few database queries, so this matters.
3. On the project dashboard click **Connect**. In the connection dialog:
   - **Branch** `production` (or `main`), **database** `neondb`, **role** the default owner
   - Turn **Connection pooling off**. The app has its own small connection pool, and Neon's pooler isn't needed
     for one server.
   - Copy the connection string. It looks like
     `postgresql://neondb_owner:xxxx@ep-cool-name-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require`
4. Keep it secret: it contains the database password. You paste it into Render in step 2.
5. Optional, to protect the free compute hours: **Branches** → `production` → the compute → **Edit**, and set
   the size to **0.25 CU** (min and max). That's enough for this app, and it can't scale up and use the hours
   faster.

You don't create any tables: the app runs the migrations itself when it starts.

### Step 2: Create the Render service

1. Go to https://render.com → **Get Started** → sign up with **GitHub** (the account that owns
   `Marinst9/voice-booking`).
2. In the dashboard click **New +** → **Blueprint**.
3. **Connect GitHub**, and give Render access to **only** the `voice-booking` repository.
4. Select `Marinst9/voice-booking`, branch `main`. Render finds `render.yaml` and shows one service:
   `voice-booking` (Free, Frankfurt).
5. Render asks for the two values marked as secret in `render.yaml`:
   - **`DATABASE_URL`:** the Neon connection string from step 1
   - **`VAPI_SECRET`:** the same value as in your Vapi Bearer Token credential (from your local `.env`, line
     `VAPI_SECRET=`)
6. Click **Deploy Blueprint** (or **Apply**). The first build takes a few minutes. In the service's **Logs** you
   should see `Running upgrade -> 0001`, `0001 -> 0002`, and `app_started` with `"environment": "production"`.

Everything else comes from `render.yaml`:

| Setting | Value | Why |
|---|---|---|
| Instance type | Free | $0 |
| Region | Frankfurt | Close to Neon's Frankfurt database |
| Health check path | `/health/live` | Doesn't touch the database, so Neon can sleep |
| Auto-deploy | **After CI checks pass** | A push to `main` deploys only when GitHub Actions passed |
| `RUN_MIGRATIONS` | `true` | The Dockerfile's start command runs `alembic upgrade head` before the app (free services have no pre-deploy command) |
| `ENVIRONMENT` | `production` | `/docs` off, HTTPS-only cookie, HSTS, refuses unsafe settings |
| `CALENDAR_BACKEND` | `postgres` | Store everything in Neon |
| `CLIENT_IP_HEADER` | `CF-Connecting-IP` | The real caller IP for rate limits (Render is behind Cloudflare, which sets it and rejects fakes) |
| `ADMIN_USERNAME` | `admin` | Dashboard login |
| `API_KEY`, `ADMIN_PASSWORD`, `SESSION_SECRET` | generated by Render | Random and strong; nothing secret is in git |
| `DATABASE_URL`, `VAPI_SECRET` | pasted by you | Secrets |

### Step 3: Get the URL and your passwords

1. The URL is at the top of the service page, e.g. **`https://voice-booking.onrender.com`** (Render adds a
   suffix like `voice-booking-ab12` if the name is taken). It's HTTPS automatically.
2. **Environment** tab: click the eye icon to show **`ADMIN_PASSWORD`** and **`API_KEY`**. Save both in a
   password manager.
3. Check it, in PowerShell:
   ```powershell
   $base = "https://voice-booking.onrender.com"
   Invoke-RestMethod "$base/health/live"      # status: ok (app is running)
   Invoke-RestMethod "$base/health"           # database: ok (Neon wakes up for this one)
   Invoke-RestMethod "$base/bookings" -Headers @{ "X-API-Key" = "<API_KEY>" }
   ```
4. Open `$base/admin` and log in with `admin` and the `ADMIN_PASSWORD`. `$base/docs` gives **404** in production
   (set `DOCS_ENABLED=true` under Environment to show it temporarily).

### Step 4: Keep it awake with cron-job.org

1. Go to https://console.cron-job.org/signup and create a free account (confirm the email).
2. **Cronjobs** → **Create cronjob**:
   - **Title:** `voice-booking keep-alive`
   - **URL:** `https://voice-booking.onrender.com/health/live` (**`/health/live`**, not `/health`, so Neon can
     sleep)
   - **Execution schedule:** **Every 10 minutes**. That's under Render's 15-minute limit, with room for one
     late ping.
   - **Notifications:** turn on **notify me when the job fails** (by email), so you hear about it when the app
     is down.
   - **Advanced** (if shown): request method `GET`, timeout 30 seconds
3. **Create**. After a few runs, the job's **History** shows status `200 OK` every 10 minutes.

Each ping is one tiny request: about 4,500 a month, well under Render's included bandwidth. In the app's logs they
appear only at `DEBUG` level, so they don't flood the logs.

### Step 5: Point Vapi at the Render URL

Replace the ngrok address in five places. The paths stay the same:

| Where in Vapi | New value |
|---|---|
| Tool `get_current_datetime` → Server URL | `https://voice-booking.onrender.com/vapi/tools` |
| Tool `check_availability` → Server URL | `https://voice-booking.onrender.com/vapi/tools` |
| Tool `create_booking` → Server URL | `https://voice-booking.onrender.com/vapi/tools` |
| Tool `cancel_booking` → Server URL | `https://voice-booking.onrender.com/vapi/tools` |
| Assistant → Advanced → Webhook Server → Server URL | `https://voice-booking.onrender.com/vapi/events` |

Use your real Render URL if it has a suffix. Keep the same credential on all five (`VAPI_SECRET` is unchanged),
**Publish** the assistant, and make a test call. The call then appears in `/admin/calls`. You can stop ngrok.

### Deploying changes

`git push`. GitHub Actions runs; if it passes, Render builds and deploys automatically (the service restarts,
offline for about a minute). If CI fails, nothing is deployed. Render's **Events** tab shows each deploy.

### Checking the free limits

| What | Where |
|---|---|
| Render free hours used this month (max 750) | Render → your workspace → **Billing** (free usage) |
| Neon compute hours (max 100) and data transfer (max 5 GB) | Neon → project → **Usage** (or **Monitoring**) |
| Is the ping working? | cron-job.org → the job → **History** |

If Neon's compute hours grow fast, something is querying the database all the time: check that the ping and
the Render health check both use `/health/live`.

### Backups (Neon)

```powershell
$url = "<the Neon connection string>"
docker run --rm -v "${PWD}:/backup" postgres:18-alpine pg_dump $url -f /backup/neon-backup.sql
```

`neon-backup.sql` is gitignored (it contains customer data). Neon can only restore the last 6 hours on the free
plan, so make a copy now and then.

### When the free deployment doesn't work

| You see | Likely cause |
|---|---|
| Deploy fails, logs show an Alembic or connection error | `DATABASE_URL` is wrong: copy it again from Neon's **Connect** dialog (pooling off), including `?sslmode=require` |
| Logs say `ENVIRONMENT=production, but: ...` | A required variable is missing; the message says which |
| Every page takes about a minute, then works | The app was asleep: check the cron-job.org job (URL, every 10 minutes, History) |
| `/health` says `"database": "unreachable"` | Neon is suspended (monthly limit reached: Neon → Usage) or the connection string changed |
| Render says the service is suspended | The 750 free hours are used up: is there another free web service in this Render workspace? |
| Push to `main` doesn't deploy | CI failed (GitHub → Actions), or auto-deploy isn't set to "After CI checks pass" (Settings → Build & Deploy) |
| `/admin` login always fails | Use the `ADMIN_PASSWORD` from Render's Environment tab, not your local one |

## Deploying to Railway

The paid option (about $5/month on Hobby; see [Hosting options](#hosting-options)). This runs the app 24/7 on
[Railway](https://railway.com), without your laptop or ngrok:

```
Caller ─> Vapi ─> https://<your-app>.up.railway.app/vapi/...  ─┐
You (browser / phone) ─> https://<your-app>.up.railway.app/admin ┤ Railway: api service (this Dockerfile)
                                                                  └──> Postgres service (private network)
git push ─> GitHub Actions (CI) ─ passes ─> Railway builds and deploys main
                                            └─ pre-deploy: alembic upgrade head
```

### What it costs

| | |
|---|---|
| **Trial** (what this guide uses) | $0. A one-time $5 credit, for 30 days, no credit card |
| This app's usage | About $4/month (API ≈ 200 MB memory, Postgres ≈ 150 MB, almost no CPU), so the trial credit should last about the 30 days |
| **After the trial** | Hobby: $5/month, which includes $5 of usage. That's enough for this app, so about $5/month in total |

These are estimates: Railway bills the memory and CPU that are actually used (about $10 per GB of memory and $20
per vCPU per month).

### What happens when the trial ends

The trial ends after **30 days or when the $5 is used up, whichever comes first**. Then:

- **Your services stop.** The voice assistant's tools stop working, and the dashboard is offline.
- **The database is kept for 30 more days, then deleted.** Railway deletes trial volumes 30 days after the credit
  runs out. Upgrading keeps the data.
- **Your options:**
  - **Upgrade to Hobby** ($5/month) and redeploy. Everything continues as before.
  - **Switch to the Free plan** ($0, $1 of usage per month). Services there **sleep** after 5–10 idle minutes
    and wake on the next request. Waking up can be slower than Vapi's 20-second tool timeout, so the first tool
    call after a quiet period may fail. Fine for a demo, not for real callers.
  - **Let it stop.** Take a backup first (see below).

### How much trial credit is left

- Railway shows the remaining trial days and credit in the dashboard: click your **workspace / account name** →
  **Usage** (it may also be under **Billing**). There you also see the cost so far and an estimate for the month.
- The cost per service (api vs Postgres) is on the same page, so you can see which one uses the most.
- Railway also emails you before the trial ends. Check once a week, it's an easy way to avoid surprises.

### Step 1: Create the Railway account

1. Go to https://railway.com and click **Sign in / Login** → **Login with GitHub**, using the GitHub account that
   owns `Marinst9/voice-booking`. Logging in with GitHub also starts Railway's automatic account verification.
2. The trial starts automatically. If you see **"Limited Trial"**, your account wasn't verified automatically
   (this depends on how old and active the GitHub account is); open https://railway.com/verify. A limited trial
   restricts outgoing connections. This app doesn't make any (it only receives requests and talks to its
   database over Railway's private network), so it should still work.

### Step 2: Create the project from your GitHub repository

1. In Railway click **New Project** → **Deploy from GitHub repo**.
2. The first time, click **Configure GitHub App** and allow Railway access to **only** the `voice-booking`
   repository (it's private, so Railway needs permission). Accept the permissions it asks for, including
   *actions / checks*, which "Wait for CI" needs.
3. Select `Marinst9/voice-booking`. Railway finds the `Dockerfile` and starts a first build.
4. **The first deploy will fail or crash. That's expected:** the database and variables don't exist yet.
5. Click the new service and rename it to `api` (Settings → the name at the top). Optional, but the rest of this
   guide calls it `api`.

### Step 3: Add the PostgreSQL database

1. On the project canvas click **+ Create** (or **+ New**) → **Database** → **PostgreSQL**.
2. Keep the name **`Postgres`**. The variables in the next step refer to it by that name.
3. Railway starts it with a volume, so the data survives restarts. There's nothing else to configure.

### Step 4: Set the environment variables

1. In PowerShell, in the project folder, run:
   ```powershell
   .\.venv\Scripts\python.exe scripts\railway_variables.py
   ```
   It prints a block of variables with **new random secrets**: production shouldn't use the same API key and
   passwords as your laptop. `VAPI_SECRET` is copied from your local `.env`, so your Vapi credential stays the same.
2. **Save `ADMIN_PASSWORD` and `API_KEY` in a password manager.** You need them later, and running the script
   again makes new ones.
3. In Railway open the **api** service → **Variables** → **Raw Editor**, paste the block, and click **Update
   Variables** (then **Deploy** if Railway asks to apply the changes).

The exact variables (and why):

| Variable | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | Safe defaults: `/docs` off, HTTPS-only cookie, HSTS, refuses unsafe settings |
| `CALENDAR_BACKEND` | `postgres` | Store bookings and calls in the database |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway fills in the database's private address (type it exactly like this) |
| `PORT` | `8080` | The port the app listens on; the public domain points to it (step 6) |
| `CLIENT_IP_HEADER` | `X-Real-IP` | The real caller IP for rate limiting (Railway's proxy sets this header) |
| `API_KEY` | random, 16+ characters | For the REST endpoints (`X-API-Key`) |
| `VAPI_SECRET` | same as your Vapi credential | Checks that requests to `/vapi/...` really come from Vapi |
| `ADMIN_USERNAME` | `admin` | Dashboard login |
| `ADMIN_PASSWORD` | random, 10+ characters | Dashboard login |
| `SESSION_SECRET` | random, 32+ characters | Signs the dashboard login cookie |

Optional (the defaults are right for production): `DOCS_ENABLED` (set `true` to show `/docs` temporarily),
`LOG_LEVEL`, `RATE_LIMIT_API_PER_MINUTE`, `RATE_LIMIT_VAPI_PER_MINUTE`, `LOGIN_MAX_FAILURES`,
`LOGIN_LOCKOUT_MINUTES`, and the shop settings (`OPEN_TIME`, `SERVICES`, ...). The app reads its settings **only
from these variables**: the Docker image contains no `.env` file.

### Step 5: Service settings (migrations, health check, deploy only after CI)

In the **api** service → **Settings**:

| Section | Setting | Value |
|---|---|---|
| Deploy | **Pre-deploy Command** | `alembic upgrade head` |
| Deploy | **Healthcheck Path** | `/health` |
| Deploy | **Restart Policy** | On Failure (the default) |
| Source | **Branch** connected to production | `main` |
| Source | **Wait for CI** | **On** |

What these do:
- **Pre-deploy command:** before each new version goes live, Railway runs the database migrations in a separate
  container with the same variables. If a migration fails, the deploy stops and the old version keeps running.
- **Healthcheck:** Railway sends traffic to a new version only after `/health` answers `200`, which includes a
  working database connection.
- **Wait for CI:** a push to `main` is deployed only after the GitHub Actions workflow (lint, all tests, and a
  production smoke test of the Docker image) has passed. If CI fails, the deploy is skipped and the current
  version stays online. The toggle only appears once Railway has the GitHub *checks* permission (step 2).

Railway used to read these settings from a `railway.json` file. That format is deprecated and stops being read on
2026-12-01, so this project keeps them in the dashboard (the table above is the "config file").

### Step 6: Get the public URL

1. **api** service → **Settings** → **Networking** → **Public Networking** → **Generate Domain**.
2. If it asks for a port, enter **`8080`** (the `PORT` variable).
3. You get an address like `https://voice-booking-production-1234.up.railway.app`. It's HTTPS automatically.

### Step 7: Check that it works

Replace `$base` with your address:

```powershell
$base = "https://voice-booking-production-1234.up.railway.app"
Invoke-RestMethod "$base/health"                 # status: ok, database: ok
Invoke-RestMethod "$base/bookings" -Headers @{ "X-API-Key" = "<API_KEY from step 4>" }
```

- `$base/admin` → log in with `admin` and the `ADMIN_PASSWORD` from step 4.
- `$base/docs` → **404**, because the docs are off in production (`DOCS_ENABLED=true` turns them on).
- In Railway, **api** → **Deployments** → **View logs** shows the JSON logs, including `app_started` with
  `"environment": "production"`.

### Step 8: Point Vapi at the new address

Change every `https://YOUR-NGROK-ADDRESS` to your Railway address. The paths stay the same:

| Where in Vapi | New value |
|---|---|
| Tool `get_current_datetime` → Server URL | `https://<your-app>.up.railway.app/vapi/tools` |
| Tool `check_availability` → Server URL | `https://<your-app>.up.railway.app/vapi/tools` |
| Tool `create_booking` → Server URL | `https://<your-app>.up.railway.app/vapi/tools` |
| Tool `cancel_booking` → Server URL | `https://<your-app>.up.railway.app/vapi/tools` |
| Assistant → Advanced → Webhook Server → Server URL | `https://<your-app>.up.railway.app/vapi/events` |

Keep the same credential on all five: `VAPI_SECRET` didn't change. **Publish** the assistant, then make a test
call (step 6 of the Vapi section). Afterwards the call is in `$base/admin/calls`. You can now stop ngrok.

### Deploying changes

```powershell
git push
```

That's all: CI runs, and if it passes Railway deploys the new version (migrations first). Watch it in GitHub's
**Actions** tab and in Railway's **Deployments** tab.

### Backups

Don't count on automatic backups on the trial: before the trial ends, and before risky changes, save a copy of the
database on your PC:

1. **Postgres** service → **Variables** → copy `DATABASE_PUBLIC_URL`.
2. In PowerShell, in the project folder (Docker Desktop must be running):
   ```powershell
   $url = "<DATABASE_PUBLIC_URL>"
   docker run --rm -v "${PWD}:/backup" postgres:18-alpine pg_dump $url -f /backup/railway-backup.sql
   ```
   `railway-backup.sql` is gitignored (it contains customer data). Make one before the trial ends.

### When the deploy doesn't work

| You see | Likely cause |
|---|---|
| Deploy fails in **Pre-deploy** | `DATABASE_URL` is wrong: it must be exactly `${{Postgres.DATABASE_URL}}`, and the database must be called `Postgres` |
| Logs say `ENVIRONMENT=production, but: ...` | A required variable is missing; the message says which |
| Health check fails / "service unavailable" | `PORT` isn't `8080`, or the domain points to a different port (step 6) |
| Deploy stays in **Waiting for CI** | CI is still running or failed (GitHub → Actions). If it stays stuck after CI passed, check Railway's GitHub App permissions, or turn Wait for CI off and on |
| `/admin` login always fails | You're using the local password. Production has its own (step 4) |
| `429 rate_limited` | Too many requests from one IP. Adjust `RATE_LIMIT_API_PER_MINUTE` if it's legitimate |
| Vapi says the tool failed | Check the tool URLs (step 8) and the **api** logs for `vapi_tool_call` / `auth_failed` |

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
`admin_logout`, `admin_login_locked`, `csrf_rejected`, `session_secret_missing`, `rate_limited`,
`health_check_failed`. Requests to `/health` and `/docs` are logged at `DEBUG` (health checks would flood the logs). All lines from one request share a `request_id`,
which is also returned in the `X-Request-ID` response header (or reused if the caller sends one).
Phone numbers are masked because they are personal data.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

1. **Lint**: `ruff check` and `ruff format --check`
2. **Tests**: all tests, against a real PostgreSQL started by GitHub
3. **Docker**: validates `docker-compose.yml`, builds the image, then tests it the way Railway runs it: migrations
   from the image against a fresh PostgreSQL, the app in `ENVIRONMENT=production` on `PORT=8080`, and checks that
   `/health` reports the database as ok, `/docs` is off and the API needs a key

Railway's **Wait for CI** deploys a push to `main` only when all three pass.

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
- **One `ENVIRONMENT` switch instead of many.** Forgetting one of five security settings on the platform is easy;
  `ENVIRONMENT=production` turns the safe defaults on together, and the app refuses to start if a setting would
  lose data (memory calendar) or weaken security (no `SESSION_SECRET`, insecure cookie).
- **Rate limits in memory.** The counters live in the app process, which is right for one replica: no Redis to
  pay for or run. With several replicas they would need a shared store. Only *failed* logins count toward the
  lockout, and a locked IP is refused even with the right password, so guessing can't continue slowly.
- **The client IP header is opt-in.** Behind Railway every request comes from Railway's proxy, so the real IP is
  read from `X-Real-IP`, which Railway's proxy sets. Locally that header is ignored, because anyone could send
  it and get around the limits.
- **Two health checks.** `/health` checks the database (for deploys on Railway, and for you). `/health/live` only
  says "the app is running" and never touches the database. Render's health check (every few seconds) and the
  keep-alive ping use it, so Neon's free database can still sleep and save its 100 monthly compute hours.
- **Migrations at startup on Render's free plan.** Free services have no pre-deploy command, so `render.yaml`
  sets `RUN_MIGRATIONS=true` and the Dockerfile's start command then runs `alembic upgrade head` before the
  app. With nothing to migrate it takes about a second; if a migration fails the app doesn't start and the
  deploy fails its health check. CI starts the image the same way against an empty database on every push.
  The command isn't in `render.yaml` (`dockerCommand`): Render passes that string to the shell with its quotes
  still on, so `sh -c "a && b"` fails with "not found".
- **Railway settings in the dashboard, not in a file.** Railway's `railway.json` is deprecated (it stops being read
  on 2026-12-01), and its replacement is applied with the Railway CLI, which needs Node.js. Five dashboard fields
  are simpler; the README lists them exactly, and the Dockerfile and CI hold everything else.
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
  rate_limit.py        per-IP rate limits and the admin login lockout
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
scripts/               railway_variables.py: prints the Railway variables with fresh secrets
migrations/            Alembic migrations
tests/                 pytest tests (conftest.py has the shared fixtures)
docker/postgres-init/  creates the booking_test database
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
render.yaml            Render Blueprint for the free deployment (Railway is set up in its dashboard)
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
