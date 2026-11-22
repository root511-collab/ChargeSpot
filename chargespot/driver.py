"""Driver features: profile, search, reserve, check-in, session, history."""
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from . import services as sv

bp = Blueprint("driver", __name__, url_prefix="/driver")
RIYADH = (24.7136, 46.6753)


@bp.route("/profile", methods=("GET", "POST"))
@login_required("driver")
def profile():
    db = get_db()
    uid = g.user["id"]
    if request.method == "POST":
        try:
            if request.form.get("form") == "vehicle":
                plate = request.form.get("plate", "").strip().upper()
                sv.validate_plate(plate)
                conn = request.form.get("connector_type")
                if conn not in sv.CONNECTORS:
                    raise sv.RuleError("Please choose a connector type.")
                bat = sv.validate_number(request.form.get("battery_kwh"), "Battery capacity", low=0, high=250)
                db.execute(
                    "INSERT INTO vehicle (driver_id, plate, connector_type, battery_kwh) VALUES (?,?,?,?) "
                    "ON CONFLICT(driver_id) DO UPDATE SET plate=excluded.plate, connector_type=excluded.connector_type, "
                    "battery_kwh=excluded.battery_kwh", (uid, plate, conn, bat))
                flash("Vehicle saved.", "ok")
            else:
                brand, last4 = sv.validate_card(request.form.get("card_number"), request.form.get("expiry"))
                db.execute(
                    "INSERT INTO payment_method (driver_id, brand, last4, expiry) VALUES (?,?,?,?) "
                    "ON CONFLICT(driver_id) DO UPDATE SET brand=excluded.brand, last4=excluded.last4, expiry=excluded.expiry",
                    (uid, brand, last4, request.form.get("expiry")))
                flash("Payment method saved (only the last 4 digits are stored).", "ok")
        except sv.RuleError as e:
            flash(str(e), "error")
        return redirect(url_for("driver.profile"))
    vehicle = db.execute("SELECT * FROM vehicle WHERE driver_id = ?", (uid,)).fetchone()
    pm = db.execute("SELECT * FROM payment_method WHERE driver_id = ?", (uid,)).fetchone()
    return render_template("driver/profile.html", vehicle=vehicle, pm=pm, connectors=sv.CONNECTORS)


@bp.route("/search")
@login_required("driver")
def search():
    db = get_db()
    vehicle = db.execute("SELECT * FROM vehicle WHERE driver_id = ?", (g.user["id"],)).fetchone()
    args = request.args
    try:
        lat = float(args.get("lat") or RIYADH[0])
        lon = float(args.get("lon") or RIYADH[1])
    except ValueError:
        lat, lon = RIYADH
    connector = args.get("connector", vehicle["connector_type"] if vehicle and "connector" not in args else "")
    max_price = float(args["max_price"]) if args.get("max_price") else None
    max_km = float(args["max_km"]) if args.get("max_km") else None
    only_av = args.get("available") == "1"
    results = sv.search_stations(lat, lon, connector or None, max_price, max_km, only_av)
    return render_template("driver/search.html", results=results, connectors=sv.CONNECTORS,
                           f={"lat": lat, "lon": lon, "connector": connector, "max_price": args.get("max_price", ""),
                              "max_km": args.get("max_km", ""), "available": only_av})


@bp.route("/station/<int:sid>")
@login_required("driver")
def station(sid):
    db = get_db()
    s = db.execute("SELECT * FROM station WHERE id = ? AND is_active = 1", (sid,)).fetchone()
    if s is None:
        flash("Station not found.", "error")
        return redirect(url_for("driver.search"))
    chargers = db.execute("SELECT * FROM charger WHERE station_id = ? AND is_active = 1 ORDER BY label", (sid,)).fetchall()
    t = sv.now()
    upcoming = {c["id"]: db.execute(
        "SELECT start_time, end_time FROM reservation WHERE charger_id = ? AND status IN ('Held','CheckedIn') "
        "AND end_time > ? ORDER BY start_time", (c["id"], sv.fmt(t))).fetchall() for c in chargers}
    default_start = (t + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
    default_end = (t + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M")
    return render_template("driver/station.html", s=s, chargers=chargers, upcoming=upcoming,
                           default_start=default_start, default_end=default_end)


@bp.route("/reserve/<int:cid>", methods=("POST",))
@login_required("driver")
def reserve(cid):
    try:
        start = sv.parse_dt(request.form.get("start"))
        end = sv.parse_dt(request.form.get("end"))
        promo = request.form.get("promo", "")
        rid, code = sv.create_reservation(g.user["id"], cid, start, end, promo)
        msg = f"Reservation confirmed. Booking confirmation: {code}"
        if promo.strip():
            p = sv.check_promo(promo)
            msg += f". Promo code {p['code']} applied: {p['percent']}% off this session"
        flash(msg + ".", "ok")
        return redirect(url_for("driver.reservations"))
    except sv.RuleError as e:
        flash(str(e), "error")
        sid = get_db().execute("SELECT station_id FROM charger WHERE id = ?", (cid,)).fetchone()
        return redirect(url_for("driver.station", sid=sid[0]) if sid else url_for("driver.search"))


def _my_reservations(where, args):
    return get_db().execute(
        "SELECT r.*, c.label, c.connector_type, c.price_per_kwh, s.name AS station, cf.code, "
        "cs.id AS session_id, cs.energy_kwh, cs.cost, cs.discount, cs.ended_at, p.status AS pay_status, "
        "(SELECT code FROM confirmation WHERE payment_id = p.id) AS pay_code "
        "FROM reservation r JOIN charger c ON c.id = r.charger_id JOIN station s ON s.id = c.station_id "
        "LEFT JOIN confirmation cf ON cf.reservation_id = r.id "
        "LEFT JOIN charging_session cs ON cs.reservation_id = r.id LEFT JOIN payment p ON p.session_id = cs.id "
        f"WHERE r.driver_id = ? {where} ORDER BY r.start_time DESC", args).fetchall()


@bp.route("/reservations")
@login_required("driver")
def reservations():
    active = _my_reservations("AND r.status IN ('Held','CheckedIn')", (g.user["id"],))
    return render_template("driver/reservations.html", active=active)


@bp.route("/reservation/<int:rid>/cancel", methods=("POST",))
@login_required("driver")
def cancel(rid):
    try:
        sv.cancel_reservation(g.user["id"], rid)
        flash("Reservation cancelled.", "ok")
    except sv.RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("driver.reservations"))


@bp.route("/reservation/<int:rid>/modify", methods=("POST",))
@login_required("driver")
def modify(rid):
    try:
        sv.modify_reservation(g.user["id"], rid, sv.parse_dt(request.form.get("start")), sv.parse_dt(request.form.get("end")))
        flash("Reservation updated.", "ok")
    except sv.RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("driver.reservations"))


@bp.route("/reservation/<int:rid>/checkin", methods=("POST",))
@login_required("driver")
def checkin(rid):
    try:
        sid = sv.check_in(g.user["id"], rid)
        flash("Checked in. Charging has started.", "ok")
        return redirect(url_for("driver.session_view", sid=sid))
    except sv.RuleError as e:
        flash(str(e), "error")
        return redirect(url_for("driver.reservations"))


@bp.route("/session/<int:sid>")
@login_required("driver")
def session_view(sid):
    s = get_db().execute(
        "SELECT cs.*, r.driver_id, r.end_time, r.promo_code, (SELECT percent FROM promo_code WHERE code = r.promo_code) AS promo_percent, c.id AS charger_id, c.label, c.price_per_kwh, c.power_kw, st.name AS station "
        "FROM charging_session cs JOIN reservation r ON r.id = cs.reservation_id JOIN charger c ON c.id = r.charger_id "
        "JOIN station st ON st.id = c.station_id WHERE cs.id = ?", (sid,)).fetchone()
    if s is None or s["driver_id"] != g.user["id"]:
        flash("Session not found.", "error")
        return redirect(url_for("driver.reservations"))
    return render_template("driver/session.html", s=s)


@bp.route("/session/<int:sid>/simulate", methods=("POST",))
@login_required("driver")
def simulate(sid):
    """Demo only: stands in for the IoT sensor message 'energy delivered'."""
    s = get_db().execute("SELECT r.charger_id, r.driver_id FROM charging_session cs JOIN reservation r "
                         "ON r.id = cs.reservation_id WHERE cs.id = ?", (sid,)).fetchone()
    if s and s["driver_id"] == g.user["id"]:
        try:
            sv.record_energy(s["charger_id"], request.form.get("kwh", 5))
        except sv.RuleError as e:
            flash(str(e), "error")
    return redirect(url_for("driver.session_view", sid=sid))


@bp.route("/session/<int:sid>/end", methods=("POST",))
@login_required("driver")
def end(sid):
    try:
        res = sv.end_session_for_driver(g.user["id"], sid)
        if res["status"] == "Paid":
            disc = f" (discount {res['discount']:.2f} SAR)" if res["discount"] else ""
            flash(f"Session ended. {res['cost']:.2f} SAR paid{disc}. Payment confirmation: {res['code']}", "ok")
        else:
            flash(f"Session ended. Payment of {res['cost']:.2f} SAR was declined; please update your payment method.", "error")
    except sv.RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("driver.history"))


@bp.route("/history")
@login_required("driver")
def history():
    rows = _my_reservations("AND r.status IN ('Completed','Cancelled','NoShow')", (g.user["id"],))
    spent = sum(r["cost"] or 0 for r in rows if r["pay_status"] == "Paid")
    energy = sum(r["energy_kwh"] or 0 for r in rows)
    return render_template("driver/history.html", rows=rows, spent=spent, energy=energy)
