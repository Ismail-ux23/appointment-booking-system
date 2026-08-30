import os
from datetime import datetime, timedelta, date as date_cls
from functools import wraps
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
from sqlalchemy import and_

from extensions import db
from models import (
    User, Provider, Service, Availability, Appointment, AppointmentLog,
    STATUS_CONFIRMED, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_NO_SHOW,
    ACTIVE_STATUSES, DAY_NAMES,
)

load_dotenv()

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-this")


def build_tidb_uri() -> str:
    """
    Build a SQLAlchemy connection string for TiDB (MySQL-compatible) from
    environment variables. Falls back to a local SQLite file if TiDB
    credentials aren't set, so the app still runs out of the box.
    """
    host = os.environ.get("TIDB_HOST", "").strip()
    user = os.environ.get("TIDB_USER", "").strip()
    password = os.environ.get("TIDB_PASSWORD", "").strip()
    placeholder_values = {
        "gateway01.ap-northeast-1.prod.aws.tidbcloud.com",
        "3MFsmdjdDfXtPi3.root",
        "U46UcQlyLQ5DOCG1",
    }
    if not host or host in placeholder_values or user in placeholder_values or password in placeholder_values:
        return "sqlite:///appointments.db"

    port = os.environ.get("TIDB_PORT", "4000")
    password = quote_plus(password)
    database = os.environ.get("TIDB_DATABASE", "appointmentbooking")
    use_ssl = os.environ.get("TIDB_SSL", "true").lower() == "true"

    uri = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    if use_ssl:
        uri += "?ssl_verify_cert=true&ssl_verify_identity=true"
    return uri


app.config["SQLALCHEMY_DATABASE_URI"] = build_tidb_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 280,
    "pool_pre_ping": True,
}

db.init_app(app)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def role_required(role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            if session.get("role") != role:
                flash("You don't have access to that page.", "error")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None


def current_provider():
    """Returns the Provider profile for the logged-in provider user, or None."""
    user = current_user()
    if user and user.role == "provider":
        return user.provider_profile
    return None


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "DAY_NAMES": DAY_NAMES}


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------

def get_available_slots(provider, service, target_date):
    """
    Compute open, bookable start times for a given provider/service/date.

    Logic: take the provider's recurring weekly Availability window(s) for
    that day of week, chop them into service-duration-sized slots, then
    remove any slot that overlaps an existing active (confirmed)
    appointment. Past slots on today's date are excluded.
    """
    day_of_week = target_date.weekday()  # Monday=0 ... Sunday=6
    windows = Availability.query.filter_by(
        provider_id=provider.id, day_of_week=day_of_week
    ).all()
    if not windows:
        return []

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    existing_appointments = Appointment.query.filter(
        Appointment.provider_id == provider.id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.start_time < day_end,
        Appointment.end_time > day_start,
    ).all()

    duration = timedelta(minutes=service.duration_minutes)
    now = datetime.now()
    slots = []

    for window in windows:
        cursor = datetime.combine(target_date, window.start_time)
        window_end = datetime.combine(target_date, window.end_time)

        while cursor + duration <= window_end:
            slot_start = cursor
            slot_end = cursor + duration

            overlaps = any(
                not (slot_end <= appt.start_time or slot_start >= appt.end_time)
                for appt in existing_appointments
            )
            is_in_past = slot_start < now

            if not overlaps and not is_in_past:
                slots.append(slot_start)

            cursor += duration

    return sorted(slots)


def slot_is_free(provider_id, start_time, end_time, exclude_appointment_id=None):
    """Re-check a specific slot is still free right before booking/rescheduling."""
    query = Appointment.query.filter(
        Appointment.provider_id == provider_id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.start_time < end_time,
        Appointment.end_time > start_time,
    )
    if exclude_appointment_id:
        query = query.filter(Appointment.id != exclude_appointment_id)
    return query.first() is None


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "client")
        business_name = request.form.get("business_name", "").strip()

        if role not in ("client", "provider"):
            role = "client"

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("signup.html")

        if role == "provider" and not business_name:
            flash("Business name is required for provider accounts.", "error")
            return render_template("signup.html")

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("signup.html")

        user = User(email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # get user.id before commit

        if role == "provider":
            provider = Provider(user_id=user.id, business_name=business_name, bio="")
            db.session.add(provider)

        db.session.commit()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            session["role"] = user.role
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    now = datetime.now()

    if user.role == "provider":
        provider = user.provider_profile
        today_start = datetime.combine(date_cls.today(), datetime.min.time())
        today_end = today_start + timedelta(days=1)

        todays_appointments = Appointment.query.filter(
            Appointment.provider_id == provider.id,
            Appointment.start_time >= today_start,
            Appointment.start_time < today_end,
            Appointment.status == STATUS_CONFIRMED,
        ).order_by(Appointment.start_time.asc()).all()

        upcoming_count = Appointment.query.filter(
            Appointment.provider_id == provider.id,
            Appointment.start_time >= now,
            Appointment.status == STATUS_CONFIRMED,
        ).count()

        service_count = Service.query.filter_by(provider_id=provider.id, is_active=True).count()

        return render_template(
            "dashboard_provider.html",
            provider=provider,
            todays_appointments=todays_appointments,
            upcoming_count=upcoming_count,
            service_count=service_count,
        )

    # client dashboard
    upcoming = Appointment.query.filter(
        Appointment.client_id == user.id,
        Appointment.start_time >= now,
        Appointment.status == STATUS_CONFIRMED,
    ).order_by(Appointment.start_time.asc()).all()

    return render_template("dashboard_client.html", upcoming=upcoming)


# ---------------------------------------------------------------------------
# Provider profile / services / availability management
# ---------------------------------------------------------------------------

@app.route("/provider/profile", methods=["GET", "POST"])
@role_required("provider")
def provider_profile():
    provider = current_provider()

    if request.method == "POST":
        provider.business_name = request.form.get("business_name", "").strip() or provider.business_name
        provider.bio = request.form.get("bio", "").strip()
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("provider_profile"))

    return render_template("provider_profile.html", provider=provider)


@app.route("/provider/services", methods=["GET", "POST"])
@role_required("provider")
def provider_services():
    provider = current_provider()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        duration = request.form.get("duration_minutes", "").strip()
        price = request.form.get("price", "").strip()

        if not name or not duration:
            flash("Service name and duration are required.", "error")
        else:
            service = Service(
                provider_id=provider.id,
                name=name,
                duration_minutes=int(duration),
                price=float(price or 0),
            )
            db.session.add(service)
            db.session.commit()
            flash(f"Service '{name}' added.", "success")
        return redirect(url_for("provider_services"))

    services = Service.query.filter_by(provider_id=provider.id).order_by(Service.name.asc()).all()
    return render_template("provider_services.html", services=services)


@app.route("/provider/services/<int:service_id>/toggle", methods=["POST"])
@role_required("provider")
def toggle_service(service_id):
    provider = current_provider()
    service = Service.query.filter_by(id=service_id, provider_id=provider.id).first_or_404()
    service.is_active = not service.is_active
    db.session.commit()
    flash(f"Service '{service.name}' is now {'active' if service.is_active else 'inactive'}.", "success")
    return redirect(url_for("provider_services"))


@app.route("/provider/services/<int:service_id>/delete", methods=["POST"])
@role_required("provider")
def delete_service(service_id):
    provider = current_provider()
    service = Service.query.filter_by(id=service_id, provider_id=provider.id).first_or_404()
    db.session.delete(service)
    db.session.commit()
    flash("Service deleted.", "success")
    return redirect(url_for("provider_services"))


@app.route("/provider/availability", methods=["GET", "POST"])
@role_required("provider")
def provider_availability():
    provider = current_provider()

    if request.method == "POST":
        day_of_week = request.form.get("day_of_week")
        start_time_str = request.form.get("start_time", "")
        end_time_str = request.form.get("end_time", "")

        try:
            start_t = datetime.strptime(start_time_str, "%H:%M").time()
            end_t = datetime.strptime(end_time_str, "%H:%M").time()
        except ValueError:
            flash("Please provide valid start and end times.", "error")
            return redirect(url_for("provider_availability"))

        if start_t >= end_t:
            flash("Start time must be before end time.", "error")
            return redirect(url_for("provider_availability"))

        slot = Availability(
            provider_id=provider.id,
            day_of_week=int(day_of_week),
            start_time=start_t,
            end_time=end_t,
        )
        db.session.add(slot)
        db.session.commit()
        flash("Availability added.", "success")
        return redirect(url_for("provider_availability"))

    windows = Availability.query.filter_by(provider_id=provider.id).order_by(
        Availability.day_of_week.asc(), Availability.start_time.asc()
    ).all()
    return render_template("provider_availability.html", windows=windows)


@app.route("/provider/availability/<int:availability_id>/delete", methods=["POST"])
@role_required("provider")
def delete_availability(availability_id):
    provider = current_provider()
    slot = Availability.query.filter_by(id=availability_id, provider_id=provider.id).first_or_404()
    db.session.delete(slot)
    db.session.commit()
    flash("Availability window removed.", "success")
    return redirect(url_for("provider_availability"))


# ---------------------------------------------------------------------------
# Client browsing & booking
# ---------------------------------------------------------------------------

@app.route("/providers")
@login_required
def browse_providers():
    providers = Provider.query.join(User).order_by(Provider.business_name.asc()).all()
    return render_template("providers.html", providers=providers)


@app.route("/providers/<int:provider_id>")
@login_required
def provider_detail(provider_id):
    provider = Provider.query.get_or_404(provider_id)
    services = Service.query.filter_by(provider_id=provider.id, is_active=True).order_by(Service.name.asc()).all()

    selected_service_id = request.args.get("service_id", type=int)
    selected_date_str = request.args.get("date", "")
    slots = []
    selected_service = None
    selected_date = None

    if selected_service_id and selected_date_str:
        selected_service = Service.query.filter_by(id=selected_service_id, provider_id=provider.id).first()
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = None

        if selected_service and selected_date and selected_date >= date_cls.today():
            slots = get_available_slots(provider, selected_service, selected_date)

    today_str = date_cls.today().isoformat()
    max_date_str = (date_cls.today() + timedelta(days=60)).isoformat()

    return render_template(
        "provider_detail.html",
        provider=provider,
        services=services,
        selected_service=selected_service,
        selected_date=selected_date,
        selected_date_str=selected_date_str,
        slots=slots,
        today_str=today_str,
        max_date_str=max_date_str,
    )


@app.route("/book/<int:provider_id>", methods=["POST"])
@role_required("client")
def book_appointment(provider_id):
    provider = Provider.query.get_or_404(provider_id)
    service_id = request.form.get("service_id", type=int)
    slot_start_str = request.form.get("slot_start", "")

    service = Service.query.filter_by(id=service_id, provider_id=provider.id, is_active=True).first()
    if not service:
        flash("That service is no longer available.", "error")
        return redirect(url_for("provider_detail", provider_id=provider.id))

    try:
        slot_start = datetime.fromisoformat(slot_start_str)
    except ValueError:
        flash("Invalid time slot.", "error")
        return redirect(url_for("provider_detail", provider_id=provider.id))

    slot_end = slot_start + timedelta(minutes=service.duration_minutes)

    if slot_start < datetime.now():
        flash("That time slot is in the past.", "error")
        return redirect(url_for("provider_detail", provider_id=provider.id))

    if not slot_is_free(provider.id, slot_start, slot_end):
        flash("Sorry, that slot was just booked by someone else. Please pick another.", "error")
        return redirect(url_for(
            "provider_detail", provider_id=provider.id,
            service_id=service.id, date=slot_start.date().isoformat()
        ))

    appointment = Appointment(
        client_id=session["user_id"],
        provider_id=provider.id,
        service_id=service.id,
        start_time=slot_start,
        end_time=slot_end,
        status=STATUS_CONFIRMED,
    )
    db.session.add(appointment)
    db.session.flush()

    log = AppointmentLog(
        appointment_id=appointment.id,
        changed_by=session["user_id"],
        action="created",
        note=f"Booked {service.name} for {slot_start.strftime('%Y-%m-%d %H:%M')}",
    )
    db.session.add(log)
    db.session.commit()

    flash("Appointment booked!", "success")
    return redirect(url_for("appointments"))


# ---------------------------------------------------------------------------
# Appointment management (shared by both roles)
# ---------------------------------------------------------------------------

@app.route("/appointments")
@login_required
def appointments():
    user = current_user()

    if user.role == "provider":
        provider = user.provider_profile
        items = Appointment.query.filter_by(provider_id=provider.id).order_by(Appointment.start_time.desc()).all()
    else:
        items = Appointment.query.filter_by(client_id=user.id).order_by(Appointment.start_time.desc()).all()

    return render_template("appointments.html", appointments=items)


def _can_manage_appointment(appointment, user):
    if user.role == "provider":
        return user.provider_profile and appointment.provider_id == user.provider_profile.id
    return appointment.client_id == user.id


@app.route("/appointments/<int:appointment_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    user = current_user()

    if not _can_manage_appointment(appointment, user):
        flash("You don't have permission to modify that appointment.", "error")
        return redirect(url_for("appointments"))

    if appointment.status != STATUS_CONFIRMED:
        flash("Only confirmed appointments can be cancelled.", "error")
        return redirect(url_for("appointments"))

    appointment.status = STATUS_CANCELLED
    log = AppointmentLog(
        appointment_id=appointment.id,
        changed_by=user.id,
        action="cancelled",
        note=request.form.get("reason", "").strip(),
    )
    db.session.add(log)
    db.session.commit()

    flash("Appointment cancelled.", "success")
    return redirect(url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/complete", methods=["POST"])
@role_required("provider")
def complete_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    user = current_user()

    if not _can_manage_appointment(appointment, user):
        flash("You don't have permission to modify that appointment.", "error")
        return redirect(url_for("appointments"))

    appointment.status = STATUS_COMPLETED
    db.session.add(AppointmentLog(appointment_id=appointment.id, changed_by=user.id, action="completed"))
    db.session.commit()
    flash("Marked as completed.", "success")
    return redirect(url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/no-show", methods=["POST"])
@role_required("provider")
def no_show_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    user = current_user()

    if not _can_manage_appointment(appointment, user):
        flash("You don't have permission to modify that appointment.", "error")
        return redirect(url_for("appointments"))

    appointment.status = STATUS_NO_SHOW
    db.session.add(AppointmentLog(appointment_id=appointment.id, changed_by=user.id, action="no_show"))
    db.session.commit()
    flash("Marked as no-show.", "success")
    return redirect(url_for("appointments"))


@app.route("/appointments/<int:appointment_id>/reschedule", methods=["GET", "POST"])
@login_required
def reschedule_appointment(appointment_id):
    appointment = Appointment.query.get_or_404(appointment_id)
    user = current_user()

    if not _can_manage_appointment(appointment, user):
        flash("You don't have permission to modify that appointment.", "error")
        return redirect(url_for("appointments"))

    if appointment.status != STATUS_CONFIRMED:
        flash("Only confirmed appointments can be rescheduled.", "error")
        return redirect(url_for("appointments"))

    provider = appointment.provider
    service = appointment.service

    if request.method == "POST":
        slot_start_str = request.form.get("slot_start", "")
        try:
            slot_start = datetime.fromisoformat(slot_start_str)
        except ValueError:
            flash("Invalid time slot.", "error")
            return redirect(url_for("reschedule_appointment", appointment_id=appointment.id))

        slot_end = slot_start + timedelta(minutes=service.duration_minutes)

        if slot_start < datetime.now():
            flash("That time slot is in the past.", "error")
            return redirect(url_for("reschedule_appointment", appointment_id=appointment.id))

        if not slot_is_free(provider.id, slot_start, slot_end, exclude_appointment_id=appointment.id):
            flash("That slot is no longer available. Please pick another.", "error")
            return redirect(url_for("reschedule_appointment", appointment_id=appointment.id))

        old_start = appointment.start_time
        appointment.start_time = slot_start
        appointment.end_time = slot_end
        db.session.add(AppointmentLog(
            appointment_id=appointment.id,
            changed_by=user.id,
            action="rescheduled",
            note=f"From {old_start.strftime('%Y-%m-%d %H:%M')} to {slot_start.strftime('%Y-%m-%d %H:%M')}",
        ))
        db.session.commit()
        flash("Appointment rescheduled.", "success")
        return redirect(url_for("appointments"))

    selected_date_str = request.args.get("date", "")
    slots = []
    selected_date = None
    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            if selected_date >= date_cls.today():
                slots = get_available_slots(provider, service, selected_date)
        except ValueError:
            selected_date = None

    today_str = date_cls.today().isoformat()
    max_date_str = (date_cls.today() + timedelta(days=60)).isoformat()

    return render_template(
        "reschedule.html",
        appointment=appointment,
        selected_date=selected_date,
        selected_date_str=selected_date_str,
        slots=slots,
        today_str=today_str,
        max_date_str=max_date_str,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5006)
