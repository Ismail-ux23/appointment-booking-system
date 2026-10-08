# Appointment Booking System

A Flask + TiDB appointment booking system with two roles — **providers**
(businesses offering appointments) and **clients** (people booking them) —
featuring computed slot availability, booking validation, rescheduling,
and a full appointment audit trail.

TiDB is MySQL-compatible, so this connects via `PyMySQL`. If you don't set
up TiDB credentials, the app automatically falls back to a local SQLite
file so it still runs out of the box.

## Features

- Two account types at signup: **client** or **provider**
- Providers set weekly recurring availability (e.g. "Mon 9am–5pm") and
  define services (name, duration, price)
- Clients browse providers, pick a service + date, and see only genuinely
  open time slots — computed by subtracting existing bookings from the
  provider's availability windows
- Booking and rescheduling accept only offered slots within working hours
  and the next 60 days, with an overlap re-check before saving
- Service durations and prices are validated; malformed weekdays and
  offset-aware timestamps are rejected
- Reschedule and cancel, both logged to an `AppointmentLog` audit trail
  (same pattern as the `StockLog` table in the Inventory Management project)
- Providers can mark appointments completed or no-show
- Role-aware dashboards: providers see today's schedule, clients see their
  upcoming bookings

## Project Structure

```
appointment_booking/
├── app.py              # Flask routes + slot-generation logic
├── models.py            # SQLAlchemy models
├── extensions.py         # Flask-SQLAlchemy instance
├── requirements.txt
├── .env.example
├── templates/            # Jinja2 HTML templates
└── static/
    └── style.css
```

---

## Running this project in Visual Studio Code

### 1. Prerequisites

- Install [Visual Studio Code](https://code.visualstudio.com/)
- Install [Python 3.10+](https://www.python.org/downloads/), added to PATH
- In VS Code, install the **Python extension** (by Microsoft)

### 2. Open the project

- Unzip `appointment_booking` somewhere on your machine
- **File → Open Folder...** and select `appointment_booking`

### 3. Create a virtual environment

Open a terminal in VS Code (`` Ctrl+` ``):

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

If VS Code asks "Select this environment for the workspace?", click **Yes**.
You can also set it manually: `Ctrl+Shift+P` → **"Python: Select Interpreter"**
→ pick the one inside `venv`.

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Connect to TiDB

1. In the [TiDB Cloud console](https://tidbcloud.com), open your cluster
   and create a database (if reusing your existing cluster, use a new
   database name so it doesn't collide with other projects):
   ```sql
   CREATE DATABASE appointment_booking;
   ```
2. Go to your cluster's **Connect** panel and copy the host, port, and
   username.
3. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   (Windows: `copy .env.example .env`)
4. Fill in your real values in `.env`:
   ```
   TIDB_HOST=gateway01.xxxxxxx.prod.aws.tidbcloud.com
   TIDB_PORT=4000
   TIDB_USER=xxxxxxx.root
   TIDB_PASSWORD=your-actual-password
   TIDB_DATABASE=appointment_booking
   TIDB_SSL=true
   SECRET_KEY=some-long-random-string
   ```
5. `.env` is already in `.gitignore` — it won't get committed.

If you skip this, the app falls back to a local `appointments.db` SQLite
file automatically. An optional `DATABASE_URL` environment variable overrides
this configuration; tests use it to select an isolated SQLite database.

### 6. Run the app

```bash
python app.py
```

Open `http://127.0.0.1:5006` in your browser. Tables are created
automatically on first run (the database itself must already exist).

### 7. First-time use

1. Go to `/signup` and create **two** accounts to test the full flow — one
   as a **provider**, one as a **client** (use different emails, or two
   browser profiles / an incognito window for the second).
2. As the provider: go to **Availability** and add at least one weekly
   window (e.g. Monday 09:00–17:00), then go to **Services** and add a
   service (e.g. "Consultation", 30 minutes).
3. As the client: go to **Find a Provider**, open the provider you created,
   pick the service and a date, and book an open slot.
4. Check **My Appointments** on both accounts to see the booking, and try
   cancelling or rescheduling it.

### Stopping the server

Press `Ctrl+C` in the terminal.

---

## Design notes

- **Slot generation**: for a given provider/service/date, the app takes
  the provider's recurring `Availability` windows for that day of week,
  slices them into `service.duration_minutes`-sized chunks, then removes
  any chunk that overlaps an existing confirmed `Appointment`. Slots in
  the past (for today's date) are excluded automatically.
- **Booking validation and concurrency**: booking and rescheduling validate
  membership in the generated availability list, then re-check overlaps
  immediately before saving. This blocks off-hours/off-grid requests and
  sequential conflicts. The check and insert are not an atomic reservation:
  simultaneous transactions can still race. Database-level reservation
  locking and concurrent integration tests are required before claiming
  guaranteed double-booking prevention.
- **Time zones**: this project uses naive local datetimes throughout (no
  timezone conversion). Fine for a single-timezone business; if you need
  multi-timezone support later, store everything in UTC and convert for
  display.
- **Audit trail**: `AppointmentLog` records every create/reschedule/cancel/
  complete/no-show action, mirroring the `StockLog` pattern from your
  Inventory Management project.

## Notes before deploying anywhere public

- Set a real, random `SECRET_KEY` via the `.env` file.
- Set `debug=False` in `app.run(...)` in `app.py`.
- Consider adding email/SMS reminders (would require an external API,
  intentionally left out of this build to keep it dependency-free).

## Possible next steps

- Email/SMS reminders before appointments
- Buffer time between bookings (e.g. 10-minute gap for cleanup)
- Recurring appointments (weekly session bookings)
- Provider working-hours exceptions (holidays, days off)
- Calendar (day/week grid) view instead of a slot list

## Automated checks

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use an isolated SQLite database and cover offered-slot validation,
booking/rescheduling ownership, sequential conflicts, invalid service
durations/prices/weekdays, inactive services, overlapping availability,
and rejection of timezone-offset timestamps. GitHub Actions runs these
checks on pushes and pull requests. Live TiDB and simultaneous bookings
remain unverified.

Slot generation returns no options for legacy services with invalid
durations. Fix those records before accepting bookings. Rescheduling
excludes the current appointment from overlap checks, so its original slot
can be selected again. The application uses local naive datetimes and does
not convert time zones.
