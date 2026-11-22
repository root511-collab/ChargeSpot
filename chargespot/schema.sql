DROP TABLE IF EXISTS confirmation;
DROP TABLE IF EXISTS promo_code;
DROP TABLE IF EXISTS payment;
DROP TABLE IF EXISTS charging_session;
DROP TABLE IF EXISTS reservation;
DROP TABLE IF EXISTS status_log;
DROP TABLE IF EXISTS station_history;
DROP TABLE IF EXISTS charger;
DROP TABLE IF EXISTS station;
DROP TABLE IF EXISTS vehicle;
DROP TABLE IF EXISTS payment_method;
DROP TABLE IF EXISTS user;

CREATE TABLE user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('driver','admin')),
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE vehicle (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER NOT NULL UNIQUE REFERENCES user(id),
  plate TEXT NOT NULL,
  connector_type TEXT NOT NULL,
  battery_kwh REAL NOT NULL CHECK (battery_kwh > 0)
);
CREATE TABLE payment_method (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER NOT NULL UNIQUE REFERENCES user(id),
  brand TEXT NOT NULL,
  last4 TEXT NOT NULL,
  expiry TEXT NOT NULL
);
CREATE TABLE station (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
  longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE station_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  station_id INTEGER NOT NULL REFERENCES station(id),
  action TEXT NOT NULL,
  details TEXT,
  changed_by INTEGER REFERENCES user(id),
  changed_at TEXT NOT NULL
);
CREATE TABLE charger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  station_id INTEGER NOT NULL REFERENCES station(id),
  label TEXT NOT NULL,
  connector_type TEXT NOT NULL,
  current_type TEXT NOT NULL CHECK (current_type IN ('AC','DC')),
  power_kw REAL NOT NULL CHECK (power_kw > 0),
  price_per_kwh REAL NOT NULL CHECK (price_per_kwh > 0),
  status TEXT NOT NULL DEFAULT 'Available' CHECK (status IN ('Available','Occupied','Reserved','Out of Service')),
  is_flagged INTEGER NOT NULL DEFAULT 0,
  flag_reason TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE status_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  charger_id INTEGER NOT NULL REFERENCES charger(id),
  old_status TEXT,
  new_status TEXT NOT NULL,
  source TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
CREATE TABLE reservation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  driver_id INTEGER NOT NULL REFERENCES user(id),
  charger_id INTEGER NOT NULL REFERENCES charger(id),
  start_time TEXT NOT NULL,
  end_time TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Held','CheckedIn','Completed','Cancelled','NoShow')),
  promo_code TEXT,
  created_at TEXT NOT NULL,
  cancelled_at TEXT
);
CREATE TABLE charging_session (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reservation_id INTEGER NOT NULL UNIQUE REFERENCES reservation(id),
  started_at TEXT NOT NULL,
  ended_at TEXT,
  energy_kwh REAL NOT NULL DEFAULT 0,
  discount REAL NOT NULL DEFAULT 0,
  cost REAL
);
CREATE TABLE payment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL UNIQUE REFERENCES charging_session(id),
  amount REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Paid','Declined')),
  gateway_ref TEXT,
  paid_at TEXT NOT NULL
);
CREATE TABLE confirmation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('Booking','Payment')),
  reservation_id INTEGER REFERENCES reservation(id),
  payment_id INTEGER REFERENCES payment(id),
  issued_at TEXT NOT NULL
);
CREATE INDEX idx_res_charger ON reservation(charger_id, start_time, end_time);
CREATE TABLE promo_code (
  code TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  percent INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 100),
  expires_on TEXT NOT NULL,
  max_uses INTEGER NOT NULL CHECK (max_uses > 0),
  used INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1
);
