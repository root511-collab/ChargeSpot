"""Demo data: five Riyadh stations, demo accounts and two weeks of sample activity."""
import random
from datetime import timedelta

from werkzeug.security import generate_password_hash

from .db import get_db
from . import services as sv

STATIONS = [
    ("ChargeSpot Olaya", "Olaya St, Al Olaya, Riyadh", 24.6948, 46.6850,
     [("O-1", "CCS2", "DC", 120, 1.10), ("O-2", "CCS2", "DC", 120, 1.10), ("O-3", "Type 2", "AC", 22, 0.75)]),
    ("ChargeSpot King Fahd Rd", "King Fahd Rd, Al Muruj, Riyadh", 24.7570, 46.6560,
     [("K-1", "CCS2", "DC", 180, 1.25), ("K-2", "CHAdeMO", "DC", 50, 1.00), ("K-3", "Type 2", "AC", 22, 0.70)]),
    ("ChargeSpot Riyadh Park", "Northern Ring Rd, Riyadh Park Mall", 24.7556, 46.6292,
     [("R-1", "Type 2", "AC", 11, 0.60), ("R-2", "Type 2", "AC", 22, 0.65), ("R-3", "CCS2", "DC", 60, 0.95)]),
    ("ChargeSpot Imam University", "Othman Bin Affan Rd, IMSIU, Riyadh", 24.8143, 46.7055,
     [("U-1", "CCS2", "DC", 60, 0.90), ("U-2", "Type 2", "AC", 22, 0.60)]),
    ("ChargeSpot Al Malaz", "Salah Al Din St, Al Malaz, Riyadh", 24.6629, 46.7322,
     [("M-1", "GB/T", "DC", 60, 0.95), ("M-2", "Type 2", "AC", 22, 0.65)]),
]

DRIVERS = [
    ("Demo Driver", "driver@chargespot.sa", "Driver1234", "ABC 1234", "CCS2", 77, "4111111111111111", "12/28"),
    ("Sara Alharbi", "sara@chargespot.sa", "Driver1234", "RSA 5521", "Type 2", 60, "5555555555554444", "06/29"),
    ("Khalid Alqahtani", "khalid@chargespot.sa", "Driver1234", "KSA 812", "CCS2", 82, "4012888888881881", "03/28"),
]


def seed():
    db = get_db()
    t = sv.now()
    db.execute("INSERT INTO user (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
               ("Platform Admin", "admin@chargespot.sa", generate_password_hash("Admin1234"), "admin", sv.fmt(t)))
    admin_id = 1
    driver_ids = []
    for name, email, pw, plate, conn, bat, card, exp in DRIVERS:
        uid = db.execute("INSERT INTO user (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                         (name, email, generate_password_hash(pw), "driver", sv.fmt(t - timedelta(days=20)))).lastrowid
        db.execute("INSERT INTO vehicle (driver_id, plate, connector_type, battery_kwh) VALUES (?,?,?,?)",
                   (uid, plate, conn, bat))
        db.execute("INSERT INTO payment_method (driver_id, brand, last4, expiry) VALUES (?,?,?,?)",
                   (uid, "Visa" if card[0] == "4" else "Mastercard", card[-4:], exp))
        driver_ids.append(uid)

    charger_ids = []
    for name, addr, lat, lon, chargers in STATIONS:
        sid = db.execute("INSERT INTO station (name, address, latitude, longitude, created_at) VALUES (?,?,?,?,?)",
                         (name, addr, lat, lon, sv.fmt(t - timedelta(days=30)))).lastrowid
        db.execute("INSERT INTO station_history (station_id, action, details, changed_by, changed_at) VALUES (?,?,?,?,?)",
                   (sid, "Created", name, admin_id, sv.fmt(t - timedelta(days=30))))
        for label, conn, cur, kw, price in chargers:
            cid = db.execute("INSERT INTO charger (station_id, label, connector_type, current_type, power_kw, price_per_kwh) "
                             "VALUES (?,?,?,?,?,?)", (sid, label, conn, cur, kw, price)).lastrowid
            charger_ids.append((cid, price, kw))

    # two weeks of completed sample sessions so the dashboard and reports have data
    rnd = random.Random(1350)
    for day in range(14, 0, -1):
        for _ in range(rnd.randint(3, 7)):
            cid, price, kw = rnd.choice(charger_ids)
            start = (t - timedelta(days=day)).replace(hour=rnd.randint(7, 21), minute=rnd.choice([0, 15, 30, 45]))
            dur = rnd.choice([30, 45, 60, 90])
            end = start + timedelta(minutes=dur)
            if not sv.charger_free(cid, start, end):
                continue
            drv = rnd.choice(driver_ids)
            created = start - timedelta(hours=rnd.randint(1, 20))
            if rnd.random() < 0.12:
                db.execute("INSERT INTO reservation (driver_id, charger_id, start_time, end_time, status, created_at, cancelled_at) "
                           "VALUES (?,?,?,?,?,?,?)", (drv, cid, sv.fmt(start), sv.fmt(end), "Cancelled", sv.fmt(created),
                                                     sv.fmt(created + timedelta(hours=1))))
                continue
            rid = db.execute("INSERT INTO reservation (driver_id, charger_id, start_time, end_time, status, created_at) "
                             "VALUES (?,?,?,?,?,?)", (drv, cid, sv.fmt(start), sv.fmt(end), "Completed", sv.fmt(created))).lastrowid
            db.execute("INSERT INTO confirmation (code, kind, reservation_id, issued_at) VALUES (?,?,?,?)",
                       (sv.new_code("BK"), "Booking", rid, sv.fmt(created)))
            energy = round(min(kw, 50) * dur / 60 * rnd.uniform(0.5, 0.9), 1)
            cost = round(energy * price, 2)
            sid_ = db.execute("INSERT INTO charging_session (reservation_id, started_at, ended_at, energy_kwh, cost) "
                              "VALUES (?,?,?,?,?)", (rid, sv.fmt(start), sv.fmt(end), energy, cost)).lastrowid
            pid = db.execute("INSERT INTO payment (session_id, amount, status, gateway_ref, paid_at) VALUES (?,?,?,?,?)",
                             (sid_, cost, "Paid", "GW-DEMO", sv.fmt(end))).lastrowid
            db.execute("INSERT INTO confirmation (code, kind, payment_id, issued_at) VALUES (?,?,?,?)",
                       (sv.new_code("PY"), "Payment", pid, sv.fmt(end)))
    # promo codes
    for code, desc, pct, exp, uses in [
        ("DRGUFRAN", "Instructor code for Prof. Gufran Ahmad Ansar – thank you for your guidance in CS1350", 100, "2027-06-30", 50),
        ("IMSIU20", "20% off for Imam University students and staff", 20, "2027-06-30", 1000),
        ("WELCOME10", "10% off the first charging session", 10, "2027-12-31", 5000),
    ]:
        db.execute("INSERT INTO promo_code (code, description, percent, expires_on, max_uses) VALUES (?,?,?,?,?)",
                   (code, desc, pct, exp, uses))
    db.execute("INSERT INTO promo_code (code, description, percent, expires_on, max_uses, is_active) VALUES (?,?,?,?,?,?)",
               ("LAUNCH50", "Launch offer (ended)", 50, "2026-10-31", 100, 1))
    # one flagged charger for the admin view
    db.execute("UPDATE charger SET is_flagged = 1, flag_reason = 'Cable damaged (reported by driver)' WHERE label = 'M-2'")
    sv.set_charger_status(db.execute("SELECT id FROM charger WHERE label='M-2'").fetchone()[0], "Out of Service", "admin")
