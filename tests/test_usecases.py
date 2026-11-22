"""Test cases for the ChargeSpot use cases.

Every test docstring follows the format used in the test plan:
TC-ID | Use case | Steps / input | Expected result
"""
from conftest import NOW, charger_id, login, query


def reserve(client, cid, start="2026-11-20T11:00", end="2026-11-20T12:00"):
    return client.post(f"/driver/reserve/{cid}", data={"start": start, "end": end}, follow_redirects=True)


# ------------------------------------------------------------------ UC1 / UC2 / UC3
def test_register_valid(client):
    """TC-01 | UC1 Register Account | Register with name 'Test User', a new email and password 'Pass1234' | Account is created and the driver can log in"""
    r = client.post("/register", data={"name": "Test User", "email": "new@test.sa", "password": "Pass1234"}, follow_redirects=True)
    assert b"Account created" in r.data
    r = login(client, "new@test.sa", "Pass1234")
    assert b"Find a charging station" in r.data


def test_register_invalid(client):
    """TC-02 | UC1 Register Account | Register with a weak password 'abc' and with an email that already exists | Both are rejected with a clear message; no account is created"""
    r = client.post("/register", data={"name": "X User", "email": "x@test.sa", "password": "abc"}, follow_redirects=True)
    assert b"Password must be" in r.data
    r = client.post("/register", data={"name": "Dup", "email": "driver@chargespot.sa", "password": "Pass1234"}, follow_redirects=True)
    assert b"already exists" in r.data


def test_login_wrong_password(client):
    """TC-03 | UC2 Log In | Log in with a correct email and a wrong password | Login is refused with 'Incorrect email or password'"""
    r = login(client, "driver@chargespot.sa", "Wrong999")
    assert b"Incorrect email or password" in r.data


def test_role_based_access(driver):
    """TC-04 | UC2 Log In (role-based access) | Logged in as a driver, open /admin/ and /admin/stations | Access is denied (HTTP 403); admin pages are only for admins"""
    assert driver.get("/admin/").status_code == 403
    assert driver.get("/admin/stations").status_code == 403


def test_profile_vehicle_and_card(driver, app):
    """TC-05 | UC3 Manage Profile and Vehicle | Save vehicle 'XYZ 77', CCS2, 64 kWh and card 4111 1111 1111 1111, 12/28 | Vehicle is saved; only the card brand and last 4 digits are stored"""
    r = driver.post("/driver/profile", data={"form": "vehicle", "plate": "XYZ 77", "connector_type": "CCS2", "battery_kwh": "64"}, follow_redirects=True)
    assert b"Vehicle saved" in r.data
    r = driver.post("/driver/profile", data={"form": "payment", "card_number": "4111 1111 1111 1111", "expiry": "12/28"}, follow_redirects=True)
    assert b"Visa" in r.data and b"1111" in r.data
    row = query(app, "SELECT * FROM payment_method WHERE driver_id = 2")[0]
    assert row["last4"] == "1111" and "4111111111111111" not in str(dict(row))


def test_profile_validation(driver):
    """TC-06 | UC3 Manage Profile and Vehicle (data validation) | Enter plate '12345', battery '-5' and card number 1234 5678 9012 3456 | Each invalid value is rejected with a message; nothing is saved"""
    r = driver.post("/driver/profile", data={"form": "vehicle", "plate": "12345", "connector_type": "CCS2", "battery_kwh": "60"}, follow_redirects=True)
    assert b"Plate number must be" in r.data
    r = driver.post("/driver/profile", data={"form": "vehicle", "plate": "ABC 1", "connector_type": "CCS2", "battery_kwh": "-5"}, follow_redirects=True)
    assert b"out of range" in r.data
    r = driver.post("/driver/profile", data={"form": "payment", "card_number": "1234 5678 9012 3456", "expiry": "12/28"}, follow_redirects=True)
    assert b"Card number is not valid" in r.data


# ------------------------------------------------------------------ UC4 / UC5
def test_search_nearest_first(driver):
    """TC-07 | UC4 Search and Filter Stations | Search from Olaya (24.6948, 46.6850) with no filters | All 5 stations are listed, nearest first (ChargeSpot Olaya at 0.0 km)"""
    r = driver.get("/driver/search?lat=24.6948&lon=46.6850&connector=")
    html = r.data.decode()
    assert "5 station(s) found" in html
    assert html.index("ChargeSpot Olaya") < html.index("ChargeSpot Imam University")


def test_search_filters(driver):
    """TC-08 | UC4 Search and Filter Stations | Filter by connector CHAdeMO; then by max price 0.65 SAR/kWh | Only King Fahd Rd has CHAdeMO; only chargers at or below 0.65 SAR/kWh are shown"""
    html = driver.get("/driver/search?connector=CHAdeMO").data.decode()
    assert "1 station(s) found" in html and "King Fahd" in html
    html = driver.get("/driver/search?connector=&max_price=0.65").data.decode()
    assert "Olaya" not in html and "Riyadh Park" in html


def test_live_status(driver, app):
    """TC-09 | UC5 View Charger Status | A sensor sets charger O-1 to Occupied; the driver opens the Olaya station | Charger O-1 is shown as Occupied to the driver"""
    cid = charger_id(app, "O-1")
    r = driver.post(f"/api/sensor/{cid}", json={"status": "Occupied"}, headers={"X-Sensor-Token": "sensor-demo-token"})
    assert r.status_code == 200
    html = driver.get("/driver/station/1").data.decode()
    assert "Charger O-1" in html and "Occupied" in html


# ------------------------------------------------------------------ UC6 / UC11 / UC12 / UC13
def test_reserve_slot(driver, app):
    """TC-10 | UC6 Reserve Charging Slot (+UC12 confirmation) | Reserve O-1 from 11:00 to 12:00 today | Reservation is saved as Held and a booking confirmation code (BK-...) is shown"""
    r = reserve(driver, charger_id(app, "O-1"))
    assert b"Reservation confirmed" in r.data and b"BK-" in r.data
    assert query(app, "SELECT status FROM reservation WHERE driver_id = 2 AND status = 'Held'")


def test_no_double_booking(client, app):
    """TC-11 | UC6 / UC11 Check Charger Availability | Driver A reserves O-1 11:00-12:00; driver B tries O-1 11:30-12:30 | Second reservation is refused: 'already booked in that time window'"""
    cid = charger_id(app, "O-1")
    login(client, "driver@chargespot.sa", "Driver1234")
    reserve(client, cid)
    client.get("/logout")
    login(client, "sara@chargespot.sa", "Driver1234")
    r = reserve(client, cid, "2026-11-20T11:30", "2026-11-20T12:30")
    assert b"already booked" in r.data


def test_reserve_rules(driver, app):
    """TC-12 | UC6 Reserve Charging Slot (rules) | Try a start time in the past, an end before the start, a 5-hour window and an out-of-service charger (M-2) | Each request is refused with its own message"""
    cid = charger_id(app, "O-1")
    assert b"cannot start in the past" in reserve(driver, cid, "2026-11-20T08:00", "2026-11-20T09:00").data
    assert b"must be after the start" in reserve(driver, cid, "2026-11-20T12:00", "2026-11-20T11:00").data
    assert b"longer than 4 hours" in reserve(driver, cid, "2026-11-20T11:00", "2026-11-20T16:00").data
    assert b"out of service" in reserve(driver, charger_id(app, "M-2")).data


def test_no_show_release(app, driver):
    """TC-13 | UC13 Release No-Show Reservation | Reserve O-1 at 10:00; the driver does not check in; time moves to 10:16 | Reservation becomes NoShow and charger O-1 returns to Available"""
    cid = charger_id(app, "O-1")
    reserve(driver, cid, "2026-11-20T10:00", "2026-11-20T11:00")
    assert query(app, "SELECT status FROM charger WHERE id = ?", (cid,))[0][0] == "Reserved"
    app.config["FAKE_NOW"] = "2026-11-20 10:16"
    driver.get("/driver/reservations")
    assert query(app, "SELECT status FROM reservation WHERE charger_id = ? ORDER BY id DESC", (cid,))[0][0] == "NoShow"
    assert query(app, "SELECT status FROM charger WHERE id = ?", (cid,))[0][0] == "Available"


# ------------------------------------------------------------------ UC7
def test_cancel_and_modify(driver, app):
    """TC-14 | UC7 Modify or Cancel Reservation | Reserve O-3 11:00-12:00, change it to 13:00-14:00, then cancel it | Times are updated, then status becomes Cancelled and the slot is free again"""
    cid = charger_id(app, "O-3")
    reserve(driver, cid)
    rid = query(app, "SELECT id FROM reservation WHERE charger_id = ? AND status='Held'", (cid,))[0][0]
    r = driver.post(f"/driver/reservation/{rid}/modify", data={"start": "2026-11-20T13:00", "end": "2026-11-20T14:00"}, follow_redirects=True)
    assert b"Reservation updated" in r.data
    assert query(app, "SELECT start_time FROM reservation WHERE id = ?", (rid,))[0][0] == "2026-11-20 13:00"
    r = driver.post(f"/driver/reservation/{rid}/cancel", follow_redirects=True)
    assert b"Reservation cancelled" in r.data
    assert query(app, "SELECT status FROM reservation WHERE id = ?", (rid,))[0][0] == "Cancelled"


# ------------------------------------------------------------------ UC8 / UC9 / UC10
def _start_session(driver, app, label="O-1"):
    cid = charger_id(app, label)
    reserve(driver, cid, "2026-11-20T10:05", "2026-11-20T11:00")
    rid = query(app, "SELECT id FROM reservation WHERE charger_id = ? AND status='Held'", (cid,))[0][0]
    driver.post(f"/driver/reservation/{rid}/checkin", follow_redirects=True)
    sid = query(app, "SELECT id FROM charging_session WHERE reservation_id = ?", (rid,))[0][0]
    return cid, rid, sid


def test_check_in_starts_session(driver, app):
    """TC-15 | UC8 Check In and Start Charging | Reserve O-1 at 10:05 and check in at 10:00 | A charging session starts and charger O-1 becomes Occupied"""
    cid, rid, sid = _start_session(driver, app)
    assert query(app, "SELECT status FROM reservation WHERE id = ?", (rid,))[0][0] == "CheckedIn"
    assert query(app, "SELECT status FROM charger WHERE id = ?", (cid,))[0][0] == "Occupied"


def test_check_in_too_early(driver, app):
    """TC-16 | UC8 Check In and Start Charging (rule) | Reserve O-1 for 13:00 and try to check in at 10:00 | Check-in is refused: it opens 15 minutes before the reserved time"""
    cid = charger_id(app, "O-1")
    reserve(driver, cid, "2026-11-20T13:00", "2026-11-20T14:00")
    rid = query(app, "SELECT id FROM reservation WHERE charger_id = ?", (cid,))[-1][0]
    r = driver.post(f"/driver/reservation/{rid}/checkin", follow_redirects=True)
    assert b"Check-in opens 15 minutes" in r.data


def test_session_cost_and_payment(driver, app):
    """TC-17 | UC9 Pay for Session (+UC12) | Sensor reports 20 kWh on O-1 (1.10 SAR/kWh); driver stops charging | Cost = 20 x 1.10 = 22.00 SAR, payment is Paid and a payment confirmation (PY-...) is issued"""
    cid, rid, sid = _start_session(driver, app)
    driver.post(f"/api/sensor/{cid}", json={"energy_kwh": 20}, headers={"X-Sensor-Token": "sensor-demo-token"})
    r = driver.post(f"/driver/session/{sid}/end", follow_redirects=True)
    assert b"22.00 SAR paid" in r.data and b"PY-" in r.data
    assert query(app, "SELECT status FROM charger WHERE id = ?", (cid,))[0][0] == "Available"


def test_auto_end_on_unplug(driver, app):
    """TC-18 | UC8 Check In and Start Charging (automatic end) | During a session on O-3 the sensor sends 12.5 kWh and then 'unplugged' | Session ends automatically; cost = 12.5 x 0.75 = 9.38 SAR"""
    cid, rid, sid = _start_session(driver, app, "O-3")
    h = {"X-Sensor-Token": "sensor-demo-token"}
    driver.post(f"/api/sensor/{cid}", json={"energy_kwh": 12.5}, headers=h)
    r = driver.post(f"/api/sensor/{cid}", json={"unplugged": True}, headers=h)
    assert r.get_json()["session_ended"] is True and r.get_json()["cost"] == 9.38


def test_payment_declined(client, app):
    """TC-19 | UC9 Pay for Session (exception) | Driver's saved card ends in 0000; session with 10 kWh ends | Payment is Declined, no payment confirmation is issued and the driver is told to update the card"""
    login(client, "driver@chargespot.sa", "Driver1234")
    query(app, "SELECT 1")
    from chargespot.db import get_db
    with app.app_context():
        get_db().execute("UPDATE payment_method SET last4 = '0000' WHERE driver_id = 2")
    cid, rid, sid = _start_session(client, app)
    client.post(f"/api/sensor/{cid}", json={"energy_kwh": 10}, headers={"X-Sensor-Token": "sensor-demo-token"})
    r = client.post(f"/driver/session/{sid}/end", follow_redirects=True)
    assert b"declined" in r.data
    assert query(app, "SELECT status FROM payment WHERE session_id = ?", (sid,))[0][0] == "Declined"


def test_history(driver, app):
    """TC-20 | UC10 View Charging History | Complete one session, then open History | The session appears with its energy, cost, payment status and confirmation code"""
    cid, rid, sid = _start_session(driver, app)
    driver.post(f"/api/sensor/{cid}", json={"energy_kwh": 8}, headers={"X-Sensor-Token": "sensor-demo-token"})
    driver.post(f"/driver/session/{sid}/end")
    html = driver.get("/driver/history").data.decode()
    assert "8.0 kWh" in html and "8.80 SAR" in html and "PY-" in html


# ------------------------------------------------------------------ UC14 - UC20 (admin)
def test_manage_stations(admin, app):
    """TC-21 | UC14 Manage Stations | Admin creates 'Test Station' (24.70, 46.70), edits its name, then deletes it | Each change is saved and recorded in the station history; deleted station is hidden from drivers but kept"""
    admin.post("/admin/stations", data={"name": "Test Station", "address": "Test Rd", "latitude": "24.70", "longitude": "46.70"})
    sid = query(app, "SELECT id FROM station WHERE name = 'Test Station'")[0][0]
    admin.post(f"/admin/station/{sid}", data={"form": "station", "name": "Test Station 2", "address": "Test Rd", "latitude": "24.70", "longitude": "46.70"})
    admin.post(f"/admin/station/{sid}", data={"form": "delete"})
    actions = [r[0] for r in query(app, "SELECT action FROM station_history WHERE station_id = ? ORDER BY id", (sid,))]
    assert actions == ["Created", "Updated", "Deleted"]
    assert query(app, "SELECT is_active FROM station WHERE id = ?", (sid,))[0][0] == 0


def test_station_validation(admin):
    """TC-22 | UC14 Manage Stations (data validation) | Create a station with latitude 120 | Station is refused: 'Latitude is out of range'"""
    r = admin.post("/admin/stations", data={"name": "Bad", "address": "Bad Rd", "latitude": "120", "longitude": "46"}, follow_redirects=True)
    assert b"Latitude is out of range" in r.data


def test_manage_charger_ports(admin, app):
    """TC-23 | UC15 Manage Charger Ports | Register port 'O-4' (CCS2, DC, 150 kW, 1.20 SAR/kWh) at Olaya and change its price to 1.15 | Port is created with all attributes; the price change is saved and logged"""
    admin.post("/admin/station/1", data={"form": "charger", "label": "O-4", "connector_type": "CCS2", "current_type": "DC", "power_kw": "150", "price_per_kwh": "1.20"})
    c = query(app, "SELECT * FROM charger WHERE label = 'O-4'")[0]
    assert (c["connector_type"], c["current_type"], c["power_kw"], c["price_per_kwh"]) == ("CCS2", "DC", 150, 1.2)
    admin.post(f"/admin/charger/{c['id']}", data={"action": "price", "price_per_kwh": "1.15"})
    assert query(app, "SELECT price_per_kwh FROM charger WHERE id = ?", (c["id"],))[0][0] == 1.15


def test_update_status_and_log(admin, app):
    """TC-24 | UC16 Update Charger Status | Admin sets K-1 to Out of Service; sensor with a wrong token tries to change it | Status changes and is written to the status log; the request without a valid token is refused (401)"""
    cid = charger_id(app, "K-1")
    admin.post(f"/admin/charger/{cid}", data={"action": "status", "status": "Out of Service"})
    assert query(app, "SELECT new_status FROM status_log WHERE charger_id = ? ORDER BY id DESC", (cid,))[0][0] == "Out of Service"
    assert admin.post(f"/api/sensor/{cid}", json={"status": "Available"}, headers={"X-Sensor-Token": "bad"}).status_code == 401


def test_manage_accounts(admin, client, app):
    """TC-25 | UC17 Manage User Accounts | Admin deactivates driver 'sara@chargespot.sa'; Sara tries to log in | Account is deactivated and Sara's login is refused"""
    uid = query(app, "SELECT id FROM user WHERE email = 'sara@chargespot.sa'")[0][0]
    admin.post("/admin/users", data={"user_id": uid})
    admin.get("/logout")
    r = login(client, "sara@chargespot.sa", "Driver1234")
    assert b"deactivated" in r.data


def test_dashboard(admin):
    """TC-26 | UC18 View Dashboard | Admin opens the dashboard | Active stations (5), sessions today, revenue today, occupancy, reservation/cancellation trend and utilization are shown"""
    html = admin.get("/admin/").data.decode()
    for text in ["Active stations", "Sessions today", "Revenue today", "occupancy", "Reservations vs cancellations", "Charger utilization"]:
        assert text in html
    assert '<b>5</b><span>Active stations' in html


def test_reports(admin):
    """TC-27 | UC19 Generate Reports | Admin opens the daily, weekly and monthly reports and downloads the CSV | Each report shows sessions, energy and revenue per charger; the CSV file downloads"""
    for p in ("daily", "weekly", "monthly"):
        r = admin.get(f"/admin/reports?period={p}")
        assert r.status_code == 200 and b"Per charger" in r.data
    r = admin.get("/admin/reports?period=monthly&format=csv")
    assert r.mimetype == "text/csv" and b"Utilization (%)" in r.data


def test_backup(admin, app):
    """TC-28 | UC20 Back Up and Restore Data | Admin presses 'Create backup now'; the backup file is opened | A backup file is created and contains all 5 stations"""
    import os, sqlite3
    r = admin.post("/admin/backup", follow_redirects=True)
    assert b"Backup created" in r.data
    files = os.listdir(app.config["BACKUP_DIR"])
    assert len(files) == 1
    con = sqlite3.connect(os.path.join(app.config["BACKUP_DIR"], files[0]))
    assert con.execute("SELECT COUNT(*) FROM station").fetchone()[0] == 5


# ------------------------------------------------------------------ promo codes (UC9)
def test_promo_code_discount(driver, app):
    """TC-29 | UC9 Pay for Session (promo code) | Reserve O-1 with promo code 'IMSIU20'; sensor reports 20 kWh at 1.10 SAR/kWh; driver stops charging | Discount 20% = 4.40 SAR; the driver pays 17.60 SAR and the code's usage count goes up by 1"""
    cid = charger_id(app, "O-1")
    driver.post(f"/driver/reserve/{cid}", data={"start": "2026-11-20T10:05", "end": "2026-11-20T11:00", "promo": "imsiu20"})
    rid = query(app, "SELECT id FROM reservation WHERE charger_id = ? AND status='Held'", (cid,))[0][0]
    driver.post(f"/driver/reservation/{rid}/checkin")
    sid = query(app, "SELECT id FROM charging_session WHERE reservation_id = ?", (rid,))[0][0]
    driver.post(f"/api/sensor/{cid}", json={"energy_kwh": 20}, headers={"X-Sensor-Token": "sensor-demo-token"})
    r = driver.post(f"/driver/session/{sid}/end", follow_redirects=True)
    assert b"17.60 SAR paid (discount 4.40 SAR)" in r.data
    assert query(app, "SELECT used FROM promo_code WHERE code = 'IMSIU20'")[0][0] == 1


def test_promo_code_rules(driver, app):
    """TC-30 | UC9 Pay for Session (promo code rules) | Reserve with an unknown code 'FREE99', with the expired code 'LAUNCH50', and with a code the admin has disabled | Each reservation is refused with its own message and no reservation is created"""
    cid = charger_id(app, "O-2")
    assert b"not valid" in driver.post(f"/driver/reserve/{cid}", data={"start": "2026-11-20T11:00", "end": "2026-11-20T12:00", "promo": "FREE99"}, follow_redirects=True).data
    assert b"has expired" in driver.post(f"/driver/reserve/{cid}", data={"start": "2026-11-20T11:00", "end": "2026-11-20T12:00", "promo": "LAUNCH50"}, follow_redirects=True).data
    from chargespot.db import get_db
    with app.app_context():
        get_db().execute("UPDATE promo_code SET is_active = 0 WHERE code = 'WELCOME10'")
    assert b"not valid" in driver.post(f"/driver/reserve/{cid}", data={"start": "2026-11-20T11:00", "end": "2026-11-20T12:00", "promo": "WELCOME10"}, follow_redirects=True).data
    assert query(app, "SELECT COUNT(*) FROM reservation WHERE charger_id = ? AND status = 'Held'", (cid,))[0][0] == 0


def test_admin_promo_codes(admin, app):
    """TC-31 | UC17 Manage User Accounts (promo codes) | Admin creates code 'RAMADAN15' (15%, 100 uses) and then creates it again; admin opens the list showing 'DRGUFRAN' | The code is created once; the duplicate is refused; all codes are listed with their usage"""
    data = {"code": "ramadan15", "description": "Ramadan offer", "percent": "15", "expires_on": "2027-03-30", "max_uses": "100"}
    assert b"RAMADAN15 created" in admin.post("/admin/promos", data=data, follow_redirects=True).data
    assert b"already exists" in admin.post("/admin/promos", data=data, follow_redirects=True).data
    html = admin.get("/admin/promos").data.decode()
    assert "DRGUFRAN" in html and "RAMADAN15" in html
