"""Sensor gateway REST API: IoT charger sensors post status and energy messages (US10, US14)."""
from flask import Blueprint, current_app, jsonify, request

from . import services as sv
from .db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


def _authorised():
    return request.headers.get("X-Sensor-Token") == current_app.config["SENSOR_TOKEN"]


@bp.route("/sensor/<int:cid>", methods=("POST",))
def sensor(cid):
    if not _authorised():
        return jsonify(error="unauthorised"), 401
    data = request.get_json(silent=True) or {}
    try:
        if "status" in data:
            sv.set_charger_status(cid, data["status"], "sensor")
        if "energy_kwh" in data:
            sv.record_energy(cid, data["energy_kwh"])
        if data.get("unplugged"):
            res = sv.sensor_unplugged(cid)
            return jsonify(ok=True, session_ended=True, **res)
    except sv.RuleError as e:
        return jsonify(error=str(e)), 400
    c = get_db().execute("SELECT status FROM charger WHERE id = ?", (cid,)).fetchone()
    return jsonify(ok=True, status=c["status"] if c else None)


@bp.route("/chargers")
def chargers():
    """Public live status of every charger (used by the driver app)."""
    rows = get_db().execute(
        "SELECT c.id, c.label, c.status, c.connector_type, c.current_type, c.price_per_kwh, s.name AS station "
        "FROM charger c JOIN station s ON s.id = c.station_id WHERE c.is_active = 1 AND s.is_active = 1").fetchall()
    return jsonify([dict(r) for r in rows])
