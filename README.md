# Appointment Booking System

A Flask + TiDB appointment booking system with two roles — **providers**
(businesses offering appointments) and **clients** (people booking them) —
featuring real-time slot availability, conflict-free booking, rescheduling,
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
- Double-booking is impossible: every booking and reschedule re-checks the
  slot is still free right before committing (handles race conditions)
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
file automatically.

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
- **Race-condition safety**: because two clients could theoretically try
  to book the same slot at nearly the same time, the booking and
  reschedule routes both re-verify the slot is free immediately before
  writing to the database, not just when the slot list was first rendered.
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
