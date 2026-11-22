"""ChargeSpot – EV Charging Station Management System (first release).

Layered architecture: templates (presentation) -> blueprints (controllers)
-> services (business rules) -> db (data access).
"""
import os
import shutil
from datetime import datetime

import click
from flask import Flask, g, session

from . import db as dbmod


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CHARGESPOT_SECRET", "dev-change-me"),
        DATABASE=os.path.join(app.instance_path, "chargespot.sqlite"),
        BACKUP_DIR=os.path.join(app.instance_path, "backups"),
        SENSOR_TOKEN=os.environ.get("CHARGESPOT_SENSOR_TOKEN", "sensor-demo-token"),
        FAKE_NOW=None,
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)

    app.teardown_appcontext(dbmod.close_db)

    from . import auth, driver, admin, api, services

    app.register_blueprint(auth.bp)
    app.register_blueprint(driver.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)

    @app.before_request
    def load_user_and_release():
        g.user = None
        uid = session.get("user_id")
        if uid is not None and os.path.exists(app.config["DATABASE"]):
            g.user = dbmod.get_db().execute(
                "SELECT * FROM user WHERE id = ? AND is_active = 1", (uid,)).fetchone()
            if g.user is None:
                session.clear()
        if os.path.exists(app.config["DATABASE"]):
            services.refresh_reserved_status()

    @app.template_filter("money")
    def money(v):
        return f"{(v or 0):,.2f} SAR"

    # ------------------------------------------------------------ CLI
    @app.cli.command("init-db")
    def init_db_command():
        """Create empty tables."""
        dbmod.init_db()
        click.echo("Database initialised.")

    @app.cli.command("seed")
    def seed_command():
        """Create tables and load demo data (Riyadh stations, demo accounts)."""
        from .seed import seed
        dbmod.init_db()
        seed()
        click.echo("Demo data loaded. Admin: admin@chargespot.sa / Admin1234  Driver: driver@chargespot.sa / Driver1234")

    @app.cli.command("backup")
    def backup_command():
        """Copy the database to the backups folder (US22)."""
        click.echo(make_backup(app))

    @app.cli.command("restore")
    @click.argument("path")
    def restore_command(path):
        """Restore the database from a backup file."""
        shutil.copy(path, app.config["DATABASE"])
        click.echo(f"Restored from {path}")

    return app


def make_backup(app):
    os.makedirs(app.config["BACKUP_DIR"], exist_ok=True)
    name = "chargespot-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite"
    dest = os.path.join(app.config["BACKUP_DIR"], name)
    import sqlite3
    src = sqlite3.connect(app.config["DATABASE"])
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    src.close(); out.close()
    return dest
