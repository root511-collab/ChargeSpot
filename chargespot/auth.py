"""Registration, login and role-based access control (US4, US5)."""
import functools

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, abort
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from . import services as sv

bp = Blueprint("auth", __name__)


def login_required(role=None):
    def deco(view):
        @functools.wraps(view)
        def wrapped(**kw):
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            if role and g.user["role"] != role:
                abort(403)
            return view(**kw)
        return wrapped
    return deco


@bp.route("/")
def index():
    if g.user is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for("admin.dashboard" if g.user["role"] == "admin" else "driver.search"))


@bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        try:
            if len(name) < 2:
                raise sv.RuleError("Please enter your name.")
            sv.validate_email(email)
            sv.validate_password(pw)
            db = get_db()
            if db.execute("SELECT 1 FROM user WHERE email = ?", (email,)).fetchone():
                raise sv.RuleError("An account with this email already exists.")
            db.execute(
                "INSERT INTO user (name, email, password_hash, role, created_at) VALUES (?,?,?,?,?)",
                (name, email, generate_password_hash(pw), "driver", sv.fmt(sv.now())),
            )
            flash("Account created. Please log in.", "ok")
            return redirect(url_for("auth.login"))
        except sv.RuleError as e:
            flash(str(e), "error")
    return render_template("auth/register.html")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM user WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], pw):
            flash("Incorrect email or password.", "error")
        elif not user["is_active"]:
            flash("This account has been deactivated by the admin.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("auth.index"))
    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
