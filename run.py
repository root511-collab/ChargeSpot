"""Shortcut: python run.py  (creates the demo database the first time)."""
import os
from chargespot import create_app
from chargespot.db import init_db
from chargespot.seed import seed

app = create_app()
if not os.path.exists(app.config["DATABASE"]):
    with app.app_context():
        init_db(); seed()
    print("Demo database created.")

if __name__ == "__main__":
    app.run(debug=False)
