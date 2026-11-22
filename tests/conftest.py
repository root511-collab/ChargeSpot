import pytest

from chargespot import create_app
from chargespot.db import init_db
from chargespot.seed import seed

NOW = "2026-11-20 10:00"


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.sqlite"),
                      "BACKUP_DIR": str(tmp_path / "backups"), "FAKE_NOW": NOW})
    with app.app_context():
        init_db()
        seed()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, email, pw):
    return client.post("/login", data={"email": email, "password": pw}, follow_redirects=True)


@pytest.fixture
def driver(client):
    login(client, "driver@chargespot.sa", "Driver1234")
    return client


@pytest.fixture
def admin(client):
    login(client, "admin@chargespot.sa", "Admin1234")
    return client


def charger_id(app, label):
    from chargespot.db import get_db
    with app.app_context():
        return get_db().execute("SELECT id FROM charger WHERE label = ?", (label,)).fetchone()[0]


def query(app, sql, args=()):
    from chargespot.db import get_db
    with app.app_context():
        return get_db().execute(sql, args).fetchall()
