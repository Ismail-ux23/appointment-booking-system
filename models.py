from datetime import datetime
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash

# Appointment lifecycle statuses
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"
STATUS_NO_SHOW = "no_show"

# Statuses that block a time slot from being booked again
ACTIVE_STATUSES = (STATUS_CONFIRMED,)

# day_of_week convention used throughout this app: Python's date.weekday(),
# i.e. Monday=0 ... Sunday=6
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # "client" or "provider"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    provider_profile = db.relationship(
        "Provider", backref="user", uselist=False, cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)


class Provider(db.Model):
    __tablename__ = "providers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    business_name = db.Column(db.String(200), nullable=False)
    bio = db.Column(db.Text)

    services = db.relationship("Service", backref="provider", lazy=True, cascade="all, delete-orphan")
    availability_slots = db.relationship(
        "Availability", backref="provider", lazy=True, cascade="all, delete-orphan"
    )
    appointments = db.relationship("Appointment", backref="provider", lazy=True)


class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey("providers.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False, default=30)
    price = db.Column(db.Float, nullable=False, default=0.0)
    is_active = db.Column(db.Boolean, default=True)

    appointments = db.relationship("Appointment", backref="service", lazy=True)


class Availability(db.Model):
    """
    A recurring weekly availability window for a provider, e.g.
    "Monday 09:00-17:00". Slot generation subtracts existing appointments
    from these windows to compute what's actually bookable.
    """
    __tablename__ = "availability"

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey("providers.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0=Monday ... 6=Sunday
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    @property
    def day_name(self):
        return DAY_NAMES[self.day_of_week]


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    provider_id = db.Column(db.Integer, db.ForeignKey("providers.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)

    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_CONFIRMED)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    client = db.relationship("User", foreign_keys=[client_id])
    logs = db.relationship(
        "AppointmentLog", backref="appointment", lazy=True, cascade="all, delete-orphan"
    )


class AppointmentLog(db.Model):
    """
    Audit trail for appointment lifecycle changes (created, rescheduled,
    cancelled, completed, no_show) — same pattern as the StockLog table
    in the Inventory Management project.
    """
    __tablename__ = "appointment_logs"

    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"), nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(30), nullable=False)  # created / rescheduled / cancelled / completed / no_show
    note = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    changed_by_user = db.relationship("User")
