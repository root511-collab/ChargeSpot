# ChargeSpot – EV Charging Station Management System (Release 1.0)

CS1350 Software Engineering 1 – Section 171 – Imam Mohammad Ibn Saud Islamic University.

ChargeSpot lets electric-vehicle drivers find nearby charging stations, see live charger status,
reserve a charger for a time window, check in, charge, and pay. Administrators manage stations,
charger ports, user accounts, the dashboard and the usage and revenue reports.

## Run it (Windows, macOS or Linux)

Requirements: Python 3.10 or newer.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
flask --app chargespot seed          # creates the database with demo data
flask --app chargespot run           # open http://127.0.0.1:5000
# or simply: python run.py
```

Demo accounts:

| Role   | Email                 | Password   |
|--------|-----------------------|------------|
| Admin  | admin@chargespot.sa   | Admin1234  |
| Driver | driver@chargespot.sa  | Driver1234 |

Run the tests: `python -m pytest -v`

With Docker: `docker build -t chargespot . && docker run -p 8000:8000 chargespot`

## What works in this release

| Area | Features | User stories |
|---|---|---|
| Accounts & security | Registration, login with hashed passwords, Driver/Admin roles, input validation | US4, US5, US7 |
| Driver profile | Vehicle (plate, connector, battery) and saved card (only brand + last 4 digits stored) | US6 |
| Stations & chargers | Create/update/delete stations, register charger ports (connector, AC/DC, power, price/kWh), station history and charger status log | US8, US9, US18 |
| Live status | Available / Occupied / Reserved / Out of Service, updated by sensor messages (`POST /api/sensor/<id>`) | US10 |
| Search | Nearby stations by distance, connector, price and availability | US11 |
| Reservations | Reserve a time window, no double booking, hold and automatic release after 15 minutes, cancel and modify | US12, US13, US16 |
| Charging & payment | Check-in, session with energy from the sensor, automatic end on unplug, cost = kWh x price, simulated payment gateway, booking and payment confirmations | US14, US15 |
| Promo codes | Driver enters a code when reserving; discount applied at payment; admin creates, limits and disables codes (demo codes: DRGUFRAN 100%, IMSIU20 20%, WELCOME10 10%) | US15 |
| History | Past reservations, sessions, costs and confirmations | US17 |
| Admin dashboard | Active stations, sessions and revenue today, occupancy, reservation/cancellation trend, utilization, flagged chargers, account management | US19, US20, US21 |
| Reports | Daily, weekly and monthly usage and revenue per charger, CSV export | US24 |
| Backup | `flask --app chargespot backup` / `restore <file>` and a button on the dashboard | US22 |
| Deployment | Dockerfile for cloud deployment | US23 |

Simulated in this release: the IoT charger sensors (Admin > IoT Simulator, or the sensor API) and the
payment gateway (cards ending in 0000 are declined). Distances are calculated from coordinates
instead of a maps API.

## Project structure

```
chargespot/
  __init__.py   app factory, CLI commands (seed, backup, restore)
  auth.py       registration, login, role-based access          (presentation/controller)
  driver.py     driver screens                                   (presentation/controller)
  admin.py      admin screens                                    (presentation/controller)
  api.py        sensor gateway REST API                          (integration)
  services.py   business rules: reservations, sessions, reports  (application layer)
  db.py         SQLite access                                    (data layer)
  schema.sql    database tables
  seed.py       demo data
  templates/    HTML pages     static/  stylesheet
tests/          31 test cases (TC-01 to TC-31) for use cases UC1 to UC20
```
