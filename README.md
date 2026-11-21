# Barber Shop Voice Booking – Backend

The backend for an AI voice booking assistant. A voice agent will later call
these endpoints as "tools". For now, bookings are stored in a **fake in-memory
calendar**, so they disappear when the server stops.

- Timezone: Europe/Skopje
- Open Monday–Saturday 09:00–18:00, closed Sunday
- Services: Haircut (30 min), Beard trim (20 min), Haircut + Beard (50 min)
- Start times are offered every 30 minutes (change `SLOT_STEP_MINUTES` in `app/config.py`)

## Project layout

```
app/
  config.py            shop settings: hours, services, timezone   <- edit this to change the shop
  booking_service.py   the rules + the sentences read to callers
  main.py              the API endpoints (thin, just call booking_service)
  models.py            the JSON shapes of requests/responses
  calendars/
    base.py            the calendar "contract" (what any calendar must do)
    memory.py          the fake in-memory calendar
tests/test_api.py      automated tests
```

## Step-by-step: run it (Windows PowerShell)

1. Open PowerShell in this folder.
2. Create a virtual environment (a private package folder for this project). You only do this once:
   ```powershell
   python -m venv .venv
   ```
3. Install the packages (also only once, or again when `requirements.txt` changes):
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. Run the tests:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest
   ```
5. Start the server:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```
   `--reload` restarts the server automatically when you change the code.
   Press **Ctrl+C** to stop it.

## Step-by-step: try it in your browser

1. With the server running, open **http://127.0.0.1:8000/docs**.
   This is an interactive page, generated automatically by FastAPI, listing every endpoint.
2. Click **POST /check_availability**, then **Try it out**.
3. Edit the example JSON, e.g.
   ```json
   { "date": "2026-11-23", "service": "Haircut" }
   ```
   and click **Execute**. The response appears below.
4. Do the same for **POST /create_booking**:
   ```json
   {
     "customer_name": "Marko",
     "phone": "+389 70 123 456",
     "service": "Haircut + Beard",
     "start_time": "2026-11-23T14:00"
   }
   ```
   Execute it twice. The second time you get a "time is already taken" message.
5. Open **http://127.0.0.1:8000/bookings** directly in a browser tab to see all bookings.
6. Try **POST /cancel_booking** with the same phone and start time.

## What the responses look like

Every response has `success` (true/false) and `message`, a sentence the voice agent can read out loud.

Success:
```json
{ "success": true, "message": "You're booked, Marko! Haircut on Monday 23 November at 14:00.", "booking": { ... } }
```

Error (HTTP 409):
```json
{ "success": false, "error": "time_taken",
  "message": "Sorry, that time is already taken. The nearest free times are 13:30 and 14:30." }
```

Error codes: `time_taken`, `closed_day`, `outside_hours`, `date_in_past`, `time_in_past`,
`unknown_service`, `invalid_phone`, `invalid_name`, `booking_not_found`, `invalid_input`.

## Input formats

- `date`: `YYYY-MM-DD`, e.g. `2026-11-23`
- `start_time`: `YYYY-MM-DDTHH:MM`, e.g. `2026-11-23T14:00`. Times without a timezone are treated as Skopje time.
- `service`: `Haircut`, `Beard trim` or `Haircut + Beard`. Variants like `haircut and beard` or `beard_trim` also work.
- `phone`: any format. Spaces and dashes are ignored when matching for cancellation.

## Swapping in Google Calendar later

1. Create `app/calendars/google.py` with a class `GoogleCalendar(CalendarBackend)` that implements the
   three methods in `app/calendars/base.py` (`list_bookings`, `add_booking`, `remove_booking`).
2. In `app/main.py`, change one line:
   ```python
   _service = BookingService(GoogleCalendar(...))
   ```
The endpoints and booking rules don't change.
