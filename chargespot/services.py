"""Business services (Application layer).

Each function holds one rule of the system so that the web routes, the
sensor API and the tests all use the same logic.
"""
import math
import re
import secrets
from datetime import datetime, timedelta

from flask import current_app

from .db import get_db

FMT = "%Y-%m-%d %H:%M"
CONNECTORS = ["Type 2", "CCS2", "CHAdeMO", "GB/T"]
STATUSES = ["Available", "Occupied", "Reserved", "Out of Service"]
NO_SHOW_MINUTES = 15
MAX_RESERVATION_HOURS = 4


class RuleError(Exception):
    """Raised when an input or a business rule is broken."""


# ---------------------------------------------------------------- time
def now():
    fake = current_app.config.get("FAKE_NOW")
    if fake:
        return datetime.strptime(fake, FMT)
    return datetime.now().replace(second=0, microsecond=0)


def fmt(dt):
    return dt.strftime(FMT)


def parse_dt(value):
    try:
        return datetime.strptime(value.replace("T", " ")[:16], FMT)
    except (ValueError, AttributeError):
        raise RuleError("Please enter a valid date and time.")


# ---------------------------------------------------------------- validation
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
PLATE_RE = re.compile(r"^[A-Za-z]{3}\s?\d{1,4}$")


def validate_email(email):
    if not EMAIL_RE.match(email or ""):
        raise RuleError("Please enter a valid email address.")


def validate_password(pw):
    if len(pw or "") < 8 or not re.search(r"\d", pw) or not re.search(r"[A-Za-z]", pw):
        raise RuleError("Password must be at least 8 characters and contain letters and numbers.")


def validate_plate(plate):
    if not PLATE_RE.match((plate or "").strip()):
        raise RuleError("Plate number must be 3 letters followed by up to 4 digits (e.g. ABC 1234).")


def luhn_ok(number):
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate_card(number, expiry):
    number = re.sub(r"\s|-", "", number or "")
    if not luhn_ok(number):
        raise RuleError("Card number is not valid.")
    m = re.match(r"^(0[1-9]|1[0-2])/(\d{2})$", expiry or "")
    if not m:
        raise RuleError("Expiry must be in MM/YY format.")
    month, year = int(m.group(1)), 2000 + int(m.group(2))
    t = now()
    if (year, month) < (t.year, t.month):
        raise RuleError("Card has expired.")
    brand = "Visa" if number.startswith("4") else "Mastercard" if number[0] == "5" else "Mada"
    return brand, number[-4:]


def validate_number(value, name, low=None, high=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise RuleError(f"{name} must be a number.")
    if (low is not None and v <= low) or (high is not None and v > high):
        raise RuleError(f"{name} is out of range.")
    return v


# ---------------------------------------------------------------- geography
def distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- charger status
def set_charger_status(charger_id, new_status, source):
    if new_status not in STATUSES:
        raise RuleError("Unknown charger status.")
    db = get_db()
    row = db.execute("SELECT status FROM charger WHERE id = ?", (charger_id,)).fetchone()
    if row is None:
        raise RuleError("Charger not found.")
    if row["status"] != new_status:
        db.execute("UPDATE charger SET status = ? WHERE id = ?", (new_status, charger_id))
        db.execute(
            "INSERT INTO status_log (charger_id, old_status, new_status, source, changed_at) VALUES (?,?,?,?,?)",
            (charger_id, row["status"], new_status, source, fmt(now())),
        )


def refresh_reserved_status():
    """Mark chargers Reserved while a held reservation window is active, and
    release reservations whose driver did not check in on time (US13)."""
    db = get_db()
    t = now()
    late = db.execute(
        "SELECT id, charger_id, start_time FROM reservation WHERE status = 'Held'"
    ).fetchall()
    for r in late:
        if parse_dt(r["start_time"]) + timedelta(minutes=NO_SHOW_MINUTES) < t:
            db.execute("UPDATE reservation SET status = 'NoShow' WHERE id = ?", (r["id"],))
            ch = db.execute("SELECT status FROM charger WHERE id = ?", (r["charger_id"],)).fetchone()
            if ch["status"] == "Reserved":
                set_charger_status(r["charger_id"], "Available", "auto-release")
    active = db.execute(
        "SELECT charger_id FROM reservation WHERE status = 'Held' AND start_time <= ? AND end_time > ?",
        (fmt(t), fmt(t)),
    ).fetchall()
    for r in active:
        ch = db.execute("SELECT status FROM charger WHERE id = ?", (r["charger_id"],)).fetchone()
        if ch["status"] == "Available":
            set_charger_status(r["charger_id"], "Reserved", "reservation hold")


def charger_free(charger_id, start, end, ignore_reservation=None):
    db = get_db()
    q = ("SELECT COUNT(*) FROM reservation WHERE charger_id = ? AND status IN ('Held','CheckedIn') "
         "AND start_time < ? AND end_time > ?")
    args = [charger_id, fmt(end), fmt(start)]
    if ignore_reservation:
        q += " AND id != ?"
        args.append(ignore_reservation)
    return db.execute(q, args).fetchone()[0] == 0


# ---------------------------------------------------------------- search
def search_stations(lat, lon, connector=None, max_price=None, max_km=None, only_available=False):
    db = get_db()
    stations = db.execute("SELECT * FROM station WHERE is_active = 1").fetchall()
    results = []
    for s in stations:
        chargers = db.execute(
            "SELECT * FROM charger WHERE station_id = ? AND is_active = 1 ORDER BY label", (s["id"],)
        ).fetchall()
        if connector:
            chargers = [c for c in chargers if c["connector_type"] == connector]
        if max_price:
            chargers = [c for c in chargers if c["price_per_kwh"] <= max_price]
        if not chargers:
            continue
        available = [c for c in chargers if c["status"] == "Available"]
        if only_available and not available:
            continue
        d = distance_km(lat, lon, s["latitude"], s["longitude"])
        if max_km and d > max_km:
            continue
        results.append({
            "station": s, "distance": round(d, 1), "chargers": chargers,
            "available": len(available), "min_price": min(c["price_per_kwh"] for c in chargers),
        })
    results.sort(key=lambda r: r["distance"])
    return results


# ---------------------------------------------------------------- reservations
def _check_window(start, end):
    t = now()
    if start < t - timedelta(minutes=1):
        raise RuleError("The reservation cannot start in the past.")
    if end <= start:
        raise RuleError("The end time must be after the start time.")
    if end - start > timedelta(hours=MAX_RESERVATION_HOURS):
        raise RuleError(f"A reservation cannot be longer than {MAX_RESERVATION_HOURS} hours.")


def new_code(prefix):
    db = get_db()
    while True:
        code = f"{prefix}-{secrets.token_hex(4).upper()}"
        if db.execute("SELECT 1 FROM confirmation WHERE code = ?", (code,)).fetchone() is None:
            return code


def check_promo(code):
    """Return the active promo code row, or raise RuleError with the reason."""
    code = (code or "").strip().upper()
    p = get_db().execute("SELECT * FROM promo_code WHERE code = ?", (code,)).fetchone()
    if p is None or not p["is_active"]:
        raise RuleError("This promo code is not valid.")
    if p["expires_on"] < fmt(now())[:10]:
        raise RuleError("This promo code has expired.")
    if p["used"] >= p["max_uses"]:
        raise RuleError("This promo code has reached its usage limit.")
    return p


def create_reservation(driver_id, charger_id, start, end, promo=None):
    _check_window(start, end)
    promo_code = check_promo(promo)["code"] if (promo or "").strip() else None
    db = get_db()
    db.execute("BEGIN IMMEDIATE")  # one writer at a time: no double booking
    try:
        ch = db.execute("SELECT * FROM charger WHERE id = ? AND is_active = 1", (charger_id,)).fetchone()
        if ch is None:
            raise RuleError("Charger not found.")
        if ch["status"] == "Out of Service":
            raise RuleError("This charger is out of service.")
        if not charger_free(charger_id, start, end):
            raise RuleError("This charger is already booked in that time window.")
        cur = db.execute(
            "INSERT INTO reservation (driver_id, charger_id, start_time, end_time, status, promo_code, created_at) VALUES (?,?,?,?,?,?,?)",
            (driver_id, charger_id, fmt(start), fmt(end), "Held", promo_code, fmt(now())),
        )
        rid = cur.lastrowid
        code = new_code("BK")
        db.execute(
            "INSERT INTO confirmation (code, kind, reservation_id, issued_at) VALUES (?,?,?,?)",
            (code, "Booking", rid, fmt(now())),
        )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    refresh_reserved_status()
    return rid, code


def get_own_reservation(driver_id, rid):
    r = get_db().execute("SELECT * FROM reservation WHERE id = ? AND driver_id = ?", (rid, driver_id)).fetchone()
    if r is None:
        raise RuleError("Reservation not found.")
    return r


def cancel_reservation(driver_id, rid):
    r = get_own_reservation(driver_id, rid)
    if r["status"] != "Held":
        raise RuleError("Only a reservation that has not started can be cancelled.")
    db = get_db()
    db.execute("UPDATE reservation SET status = 'Cancelled', cancelled_at = ? WHERE id = ?", (fmt(now()), rid))
    ch = db.execute("SELECT status FROM charger WHERE id = ?", (r["charger_id"],)).fetchone()
    if ch["status"] == "Reserved":
        set_charger_status(r["charger_id"], "Available", "cancellation")


def modify_reservation(driver_id, rid, start, end):
    r = get_own_reservation(driver_id, rid)
    if r["status"] != "Held":
        raise RuleError("Only a reservation that has not started can be changed.")
    _check_window(start, end)
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        if not charger_free(r["charger_id"], start, end, ignore_reservation=rid):
            raise RuleError("This charger is already booked in that time window.")
        db.execute("UPDATE reservation SET start_time = ?, end_time = ? WHERE id = ?", (fmt(start), fmt(end), rid))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    ch = db.execute("SELECT status FROM charger WHERE id = ?", (r["charger_id"],)).fetchone()
    if ch["status"] == "Reserved":
        set_charger_status(r["charger_id"], "Available", "modification")
    refresh_reserved_status()


# ---------------------------------------------------------------- charging sessions
def check_in(driver_id, rid):
    r = get_own_reservation(driver_id, rid)
    if r["status"] != "Held":
        raise RuleError("This reservation cannot be checked in.")
    t = now()
    start = parse_dt(r["start_time"])
    if t < start - timedelta(minutes=NO_SHOW_MINUTES):
        raise RuleError(f"Check-in opens {NO_SHOW_MINUTES} minutes before the reserved time.")
    if t > start + timedelta(minutes=NO_SHOW_MINUTES):
        raise RuleError("The check-in time has passed.")
    db = get_db()
    db.execute("UPDATE reservation SET status = 'CheckedIn' WHERE id = ?", (rid,))
    cur = db.execute("INSERT INTO charging_session (reservation_id, started_at) VALUES (?,?)", (rid, fmt(t)))
    set_charger_status(r["charger_id"], "Occupied", "check-in")
    return cur.lastrowid


def record_energy(charger_id, energy_kwh):
    """Sensor message: add delivered energy to the active session of a charger."""
    energy = validate_number(energy_kwh, "Energy", low=0, high=500)
    db = get_db()
    s = db.execute(
        "SELECT cs.id FROM charging_session cs JOIN reservation r ON r.id = cs.reservation_id "
        "WHERE r.charger_id = ? AND cs.ended_at IS NULL", (charger_id,)
    ).fetchone()
    if s is None:
        raise RuleError("No active session on this charger.")
    db.execute("UPDATE charging_session SET energy_kwh = energy_kwh + ? WHERE id = ?", (energy, s["id"]))
    return s["id"]


def gateway_charge(payment_method, amount):
    """Simulated external payment gateway (adapter). Cards ending in 0000 are declined."""
    if payment_method is None:
        return "Declined", None
    if payment_method["last4"] == "0000":
        return "Declined", None
    return "Paid", "GW-" + secrets.token_hex(4).upper()


def end_session(session_id):
    db = get_db()
    s = db.execute(
        "SELECT cs.*, r.driver_id, r.charger_id, r.id AS rid, r.promo_code FROM charging_session cs "
        "JOIN reservation r ON r.id = cs.reservation_id WHERE cs.id = ?", (session_id,)
    ).fetchone()
    if s is None or s["ended_at"]:
        raise RuleError("This session is not active.")
    price = db.execute("SELECT price_per_kwh FROM charger WHERE id = ?", (s["charger_id"],)).fetchone()[0]
    gross = round(s["energy_kwh"] * price, 2)
    discount = 0.0
    if s["promo_code"]:
        p = db.execute("SELECT * FROM promo_code WHERE code = ?", (s["promo_code"],)).fetchone()
        if p and p["is_active"] and p["used"] < p["max_uses"]:
            discount = round(gross * p["percent"] / 100, 2)
            db.execute("UPDATE promo_code SET used = used + 1 WHERE code = ?", (p["code"],))
    cost = round(gross - discount, 2)
    db.execute("UPDATE charging_session SET ended_at = ?, discount = ?, cost = ? WHERE id = ?",
               (fmt(now()), discount, cost, session_id))
    db.execute("UPDATE reservation SET status = 'Completed' WHERE id = ?", (s["rid"],))
    set_charger_status(s["charger_id"], "Available", "session end")
    pm = db.execute("SELECT * FROM payment_method WHERE driver_id = ?", (s["driver_id"],)).fetchone()
    status, ref = ("Paid", "FREE") if cost == 0 else gateway_charge(pm, cost)
    cur = db.execute(
        "INSERT INTO payment (session_id, amount, status, gateway_ref, paid_at) VALUES (?,?,?,?,?)",
        (session_id, cost, status, ref, fmt(now())),
    )
    code = None
    if status == "Paid" or cost == 0:
        status = "Paid"
        code = new_code("PY")
        db.execute(
            "INSERT INTO confirmation (code, kind, payment_id, issued_at) VALUES (?,?,?,?)",
            (code, "Payment", cur.lastrowid, fmt(now())),
        )
    return {"cost": cost, "discount": discount, "status": status, "code": code}


def end_session_for_driver(driver_id, session_id):
    s = get_db().execute(
        "SELECT r.driver_id FROM charging_session cs JOIN reservation r ON r.id = cs.reservation_id WHERE cs.id = ?",
        (session_id,),
    ).fetchone()
    if s is None or s["driver_id"] != driver_id:
        raise RuleError("Session not found.")
    return end_session(session_id)


def sensor_unplugged(charger_id):
    """Sensor reports the cable was unplugged: the session ends automatically."""
    db = get_db()
    s = db.execute(
        "SELECT cs.id FROM charging_session cs JOIN reservation r ON r.id = cs.reservation_id "
        "WHERE r.charger_id = ? AND cs.ended_at IS NULL", (charger_id,)
    ).fetchone()
    if s is None:
        raise RuleError("No active session on this charger.")
    return end_session(s["id"])


# ---------------------------------------------------------------- dashboard & reports
def dashboard_stats():
    db = get_db()
    today = fmt(now())[:10]
    total = db.execute("SELECT COUNT(*) FROM charger WHERE is_active = 1").fetchone()[0] or 1
    by_status = {s: 0 for s in STATUSES}
    for row in db.execute("SELECT status, COUNT(*) c FROM charger WHERE is_active = 1 GROUP BY status"):
        by_status[row["status"]] = row["c"]
    return {
        "active_stations": db.execute("SELECT COUNT(*) FROM station WHERE is_active = 1").fetchone()[0],
        "chargers": total,
        "sessions_today": db.execute(
            "SELECT COUNT(*) FROM charging_session WHERE substr(started_at,1,10) = ?", (today,)).fetchone()[0],
        "revenue_today": db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payment WHERE status='Paid' AND substr(paid_at,1,10) = ?",
            (today,)).fetchone()[0],
        "by_status": by_status,
        "occupancy": round(100 * (by_status["Occupied"] + by_status["Reserved"]) / total, 1),
    }


def trends(days=14):
    db = get_db()
    t = now()
    out = []
    for i in range(days - 1, -1, -1):
        d = fmt(t - timedelta(days=i))[:10]
        made = db.execute("SELECT COUNT(*) FROM reservation WHERE substr(created_at,1,10) = ?", (d,)).fetchone()[0]
        canc = db.execute("SELECT COUNT(*) FROM reservation WHERE substr(cancelled_at,1,10) = ?", (d,)).fetchone()[0]
        out.append({"day": d, "reservations": made, "cancellations": canc})
    return out


def utilization(start, end):
    """Hours of charging per charger in [start, end) divided by the operating hours
    in the period (stations operate 16 hours a day, 06:00-22:00)."""
    db = get_db()
    hours = max((end - start).total_seconds() / 3600 * 16 / 24, 1)
    rows = db.execute(
        "SELECT c.id, c.label, s.name AS station, c.price_per_kwh FROM charger c JOIN station s ON s.id = c.station_id "
        "WHERE c.is_active = 1 ORDER BY s.name, c.label").fetchall()
    out = []
    for c in rows:
        sess = db.execute(
            "SELECT cs.started_at, cs.ended_at, cs.energy_kwh, cs.cost FROM charging_session cs "
            "JOIN reservation r ON r.id = cs.reservation_id WHERE r.charger_id = ? AND cs.ended_at IS NOT NULL "
            "AND cs.started_at >= ? AND cs.started_at < ?", (c["id"], fmt(start), fmt(end))).fetchall()
        used = sum((parse_dt(x["ended_at"]) - parse_dt(x["started_at"])).total_seconds() / 3600 for x in sess)
        out.append({
            "station": c["station"], "charger": c["label"], "sessions": len(sess),
            "energy": round(sum(x["energy_kwh"] for x in sess), 1),
            "revenue": round(sum(x["cost"] or 0 for x in sess), 2),
            "utilization": round(100 * used / hours, 1),
        })
    return out


def period_bounds(period):
    t = now()
    day = t.replace(hour=0, minute=0)
    if period == "daily":
        return day, day + timedelta(days=1)
    if period == "weekly":
        start = day - timedelta(days=6)
        return start, day + timedelta(days=1)
    if period == "monthly":
        start = day - timedelta(days=29)
        return start, day + timedelta(days=1)
    raise RuleError("Unknown report period.")


def usage_report(period):
    start, end = period_bounds(period)
    rows = utilization(start, end)
    return {
        "period": period, "start": fmt(start)[:10], "end": fmt(end - timedelta(days=1))[:10], "rows": rows,
        "total_sessions": sum(r["sessions"] for r in rows),
        "total_energy": round(sum(r["energy"] for r in rows), 1),
        "total_revenue": round(sum(r["revenue"] for r in rows), 2),
    }
