"""Admin features: stations, chargers, accounts, dashboard, reports, backup."""
import csv
import io
import json

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from . import services as sv

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _history(station_id, action, details):
    get_db().execute(
        "INSERT INTO station_history (station_id, action, details, changed_by, changed_at) VALUES (?,?,?,?,?)",
        (station_id, action, details, g.user["id"], sv.fmt(sv.now())))


@bp.route("/")
@login_required("admin")
def dashboard():
    stats = sv.dashboard_stats()
    start, end = sv.period_bounds("weekly")
    util = sv.utilization(start, end)
    flagged = get_db().execute(
        "SELECT c.*, s.name AS station FROM charger c JOIN station s ON s.id = c.station_id "
        "WHERE c.is_active = 1 AND (c.is_flagged = 1 OR c.status = 'Out of Service')").fetchall()
    return render_template("admin/dashboard.html", stats=stats, trends=sv.trends(), util=util, flagged=flagged)


# ------------------------------------------------------------------ stations
def _station_form():
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip()
    if len(name) < 3 or len(address) < 3:
        raise sv.RuleError("Name and address are required.")
    lat = sv.validate_number(request.form.get("latitude"), "Latitude", low=-90, high=90)
    lon = sv.validate_number(request.form.get("longitude"), "Longitude", low=-180, high=180)
    return name, address, lat, lon


@bp.route("/stations", methods=("GET", "POST"))
@login_required("admin")
def stations():
    db = get_db()
    if request.method == "POST":
        try:
            name, address, lat, lon = _station_form()
            cur = db.execute("INSERT INTO station (name, address, latitude, longitude, created_at) VALUES (?,?,?,?,?)",
                             (name, address, lat, lon, sv.fmt(sv.now())))
            _history(cur.lastrowid, "Created", name)
            flash("Station created.", "ok")
        except sv.RuleError as e:
            flash(str(e), "error")
        return redirect(url_for("admin.stations"))
    rows = db.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM charger c WHERE c.station_id = s.id AND c.is_active = 1) AS n "
        "FROM station s ORDER BY s.is_active DESC, s.name").fetchall()
    return render_template("admin/stations.html", rows=rows)


@bp.route("/station/<int:sid>", methods=("GET", "POST"))
@login_required("admin")
def station(sid):
    db = get_db()
    s = db.execute("SELECT * FROM station WHERE id = ?", (sid,)).fetchone()
    if s is None:
        flash("Station not found.", "error")
        return redirect(url_for("admin.stations"))
    if request.method == "POST":
        try:
            form = request.form.get("form")
            if form == "station":
                name, address, lat, lon = _station_form()
                db.execute("UPDATE station SET name=?, address=?, latitude=?, longitude=? WHERE id=?",
                           (name, address, lat, lon, sid))
                _history(sid, "Updated", json.dumps({"name": name, "address": address, "lat": lat, "lon": lon}))
                flash("Station updated.", "ok")
            elif form == "delete":
                db.execute("UPDATE station SET is_active = 0 WHERE id = ?", (sid,))
                db.execute("UPDATE charger SET is_active = 0 WHERE station_id = ?", (sid,))
                _history(sid, "Deleted", "Station and its chargers removed from service (records kept)")
                flash("Station deleted. Its history is kept for reports.", "ok")
                return redirect(url_for("admin.stations"))
            elif form == "charger":
                label = request.form.get("label", "").strip()
                conn = request.form.get("connector_type")
                cur_type = request.form.get("current_type")
                if not label or conn not in sv.CONNECTORS or cur_type not in ("AC", "DC"):
                    raise sv.RuleError("Please fill in the label, connector and AC/DC type.")
                power = sv.validate_number(request.form.get("power_kw"), "Power", low=0, high=400)
                price = sv.validate_number(request.form.get("price_per_kwh"), "Price per kWh", low=0, high=20)
                db.execute("INSERT INTO charger (station_id, label, connector_type, current_type, power_kw, price_per_kwh) "
                           "VALUES (?,?,?,?,?,?)", (sid, label, conn, cur_type, power, price))
                _history(sid, "Charger added", f"{label} {conn} {cur_type} {power} kW {price} SAR/kWh")
                flash("Charger port registered.", "ok")
        except sv.RuleError as e:
            flash(str(e), "error")
        return redirect(url_for("admin.station", sid=sid))
    chargers = db.execute("SELECT * FROM charger WHERE station_id = ? ORDER BY is_active DESC, label", (sid,)).fetchall()
    hist = db.execute("SELECT h.*, u.name AS who FROM station_history h LEFT JOIN user u ON u.id = h.changed_by "
                      "WHERE station_id = ? ORDER BY h.id DESC", (sid,)).fetchall()
    logs = db.execute("SELECT l.*, c.label FROM status_log l JOIN charger c ON c.id = l.charger_id "
                      "WHERE c.station_id = ? ORDER BY l.id DESC LIMIT 15", (sid,)).fetchall()
    return render_template("admin/station.html", s=s, chargers=chargers, hist=hist, logs=logs,
                           connectors=sv.CONNECTORS, statuses=sv.STATUSES)


@bp.route("/charger/<int:cid>", methods=("POST",))
@login_required("admin")
def charger(cid):
    db = get_db()
    c = db.execute("SELECT * FROM charger WHERE id = ?", (cid,)).fetchone()
    if c is None:
        flash("Charger not found.", "error")
        return redirect(url_for("admin.stations"))
    try:
        action = request.form.get("action")
        if action == "status":
            sv.set_charger_status(cid, request.form.get("status"), "admin")
            flash("Charger status updated.", "ok")
        elif action == "price":
            price = sv.validate_number(request.form.get("price_per_kwh"), "Price per kWh", low=0, high=20)
            db.execute("UPDATE charger SET price_per_kwh = ? WHERE id = ?", (price, cid))
            _history(c["station_id"], "Price changed", f"{c['label']}: {c['price_per_kwh']} -> {price} SAR/kWh")
            flash("Price updated.", "ok")
        elif action == "flag":
            db.execute("UPDATE charger SET is_flagged = 1, flag_reason = ? WHERE id = ?",
                       (request.form.get("reason", "Reported fault").strip() or "Reported fault", cid))
            flash("Charger flagged.", "ok")
        elif action == "unflag":
            db.execute("UPDATE charger SET is_flagged = 0, flag_reason = NULL WHERE id = ?", (cid,))
            flash("Flag removed.", "ok")
        elif action == "remove":
            db.execute("UPDATE charger SET is_active = 0 WHERE id = ?", (cid,))
            _history(c["station_id"], "Charger removed", c["label"])
            flash("Charger removed (history kept).", "ok")
    except sv.RuleError as e:
        flash(str(e), "error")
    return redirect(request.form.get("back") or url_for("admin.station", sid=c["station_id"]))


# ------------------------------------------------------------------ accounts
@bp.route("/users", methods=("GET", "POST"))
@login_required("admin")
def users():
    db = get_db()
    if request.method == "POST":
        uid = int(request.form.get("user_id"))
        if uid == g.user["id"]:
            flash("You cannot deactivate your own account.", "error")
        else:
            db.execute("UPDATE user SET is_active = 1 - is_active WHERE id = ?", (uid,))
            flash("Account updated.", "ok")
        return redirect(url_for("admin.users"))
    rows = db.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM reservation r WHERE r.driver_id = u.id) AS res, "
        "(SELECT COUNT(*) FROM reservation r WHERE r.driver_id = u.id AND r.status = 'NoShow') AS noshow "
        "FROM user u ORDER BY u.role, u.name").fetchall()
    return render_template("admin/users.html", rows=rows)


# ------------------------------------------------------------------ reports
@bp.route("/reports")
@login_required("admin")
def reports():
    period = request.args.get("period", "weekly")
    try:
        rep = sv.usage_report(period)
    except sv.RuleError:
        rep = sv.usage_report("weekly")
    if request.args.get("format") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Station", "Charger", "Sessions", "Energy (kWh)", "Revenue (SAR)", "Utilization (%)"])
        for r in rep["rows"]:
            w.writerow([r["station"], r["charger"], r["sessions"], r["energy"], r["revenue"], r["utilization"]])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=chargespot-{rep['period']}-report.csv"})
    return render_template("admin/reports.html", rep=rep)


# ------------------------------------------------------------------ promo codes
@bp.route("/promos", methods=("GET", "POST"))
@login_required("admin")
def promos():
    db = get_db()
    if request.method == "POST":
        try:
            if request.form.get("action") == "toggle":
                db.execute("UPDATE promo_code SET is_active = 1 - is_active WHERE code = ?", (request.form.get("code"),))
                flash("Promo code updated.", "ok")
            else:
                code = request.form.get("code", "").strip().upper()
                import re as _re
                if not _re.match(r"^[A-Z0-9-]{4,20}$", code):
                    raise sv.RuleError("Code must be 4-20 letters, numbers or dashes.")
                if db.execute("SELECT 1 FROM promo_code WHERE code = ?", (code,)).fetchone():
                    raise sv.RuleError("This code already exists.")
                pct = int(sv.validate_number(request.form.get("percent"), "Discount", low=0, high=100))
                uses = int(sv.validate_number(request.form.get("max_uses"), "Maximum uses", low=0, high=100000))
                exp = sv.parse_dt((request.form.get("expires_on") or "") + " 00:00").strftime("%Y-%m-%d")
                desc = request.form.get("description", "").strip() or "Promotion"
                db.execute("INSERT INTO promo_code (code, description, percent, expires_on, max_uses) VALUES (?,?,?,?,?)",
                           (code, desc, pct, exp, uses))
                flash(f"Promo code {code} created.", "ok")
        except sv.RuleError as e:
            flash(str(e), "error")
        return redirect(url_for("admin.promos"))
    rows = db.execute("SELECT * FROM promo_code ORDER BY is_active DESC, code").fetchall()
    return render_template("admin/promos.html", rows=rows)


# ------------------------------------------------------------------ IoT simulator & backup
@bp.route("/simulator", methods=("GET", "POST"))
@login_required("admin")
def simulator():
    db = get_db()
    if request.method == "POST":
        cid = int(request.form.get("charger_id"))
        try:
            kind = request.form.get("kind")
            if kind == "status":
                sv.set_charger_status(cid, request.form.get("status"), "sensor")
                flash("Sensor status message processed.", "ok")
            elif kind == "energy":
                sv.record_energy(cid, request.form.get("kwh"))
                flash("Energy reading processed.", "ok")
            elif kind == "unplug":
                res = sv.sensor_unplugged(cid)
                flash(f"Session ended by sensor. Cost {res['cost']:.2f} SAR ({res['status']}).", "ok")
        except sv.RuleError as e:
            flash(str(e), "error")
        return redirect(url_for("admin.simulator"))
    chargers = db.execute(
        "SELECT c.*, s.name AS station, (SELECT cs.energy_kwh FROM charging_session cs JOIN reservation r "
        "ON r.id = cs.reservation_id WHERE r.charger_id = c.id AND cs.ended_at IS NULL) AS live_kwh "
        "FROM charger c JOIN station s ON s.id = c.station_id WHERE c.is_active = 1 ORDER BY s.name, c.label").fetchall()
    return render_template("admin/simulator.html", chargers=chargers, statuses=sv.STATUSES,
                           token=current_app.config["SENSOR_TOKEN"])


@bp.route("/backup", methods=("POST",))
@login_required("admin")
def backup():
    from . import make_backup
    path = make_backup(current_app)
    flash(f"Backup created: {path.split('/')[-1]}", "ok")
    return redirect(url_for("admin.dashboard"))
