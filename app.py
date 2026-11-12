"""SkyLink dashboard server.
Reads the existing SQL views (read-only) and serves them as JSON at /api/<view>.
Also lets you store new data (records, passengers, bookings, flights, payments).
Run:  python app.py      then open http://127.0.0.1:5000
"""
import os, decimal, datetime, uuid
from flask import Flask, jsonify, request, send_from_directory, abort
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

# ================= EDIT ONLY THIS BLOCK =================
DB_USER     = "skylink_app"
DB_PASSWORD = "SkyLink2026dw"   # symbols such as @ are fine here
DB_HOST     = "127.0.0.1"
DB_PORT     = 3306
DB_NAME     = "skylink"               # your database name in phpMyAdmin
# ========================================================

DB_URL = f"{DB_HOST}:{DB_PORT}/{DB_NAME}"   # shown by check_db.py (no password)
engine = create_engine(
    URL.create("mysql+pymysql", username=DB_USER, password=DB_PASSWORD,
               host=DB_HOST, port=DB_PORT, database=DB_NAME),
    pool_pre_ping=True)

# Only these views can be requested (nothing else is reachable)
VIEWS = [
    "vw_kpi_executive", "vw_revenue_by_destination", "vw_monthly_trend",
    "vw_occupancy_by_flight", "vw_delay_share", "vw_revenue_summary", "vw_payment_mix",
    "vw_customer_kpi", "vw_repeat_passengers", "vw_tier_mix", "vw_nationality_mix",
    "vw_flights_today", "vw_alerts_today", "vw_unpaid_imminent",
    "vw_etl_audit", "vw_reject_queue", "vw_integrity_check",
]

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))

def clean(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time, datetime.timedelta)): return str(v)
    return v

@app.route("/")
def index():
    return send_from_directory(HERE, "index.html")

@app.route("/api/<view>")
def api(view):
    if view not in VIEWS:
        abort(404)
    try:
        with engine.connect() as conn:
            res = conn.execute(text(f"SELECT * FROM {view}"))
            cols = [c.lower() for c in res.keys()]
            return jsonify([{c: clean(v) for c, v in zip(cols, row)} for row in res.fetchall()])
    except Exception as e:
        app.logger.error("View %s failed: %s", view, e)
        return jsonify({"error": f"{view} failed"}), 500

# ---------------------------------------------------------------------------
# Write endpoints: store information that can be retrieved later
# ---------------------------------------------------------------------------
def _payload():
    return request.get_json(silent=True) or {}

def _s(data, key, default=""):
    return str(data.get(key) or default).strip()

def _ensure_date(conn, iso):
    """Return a DateKey for iso, adding the row to dim_date if needed."""
    d = datetime.date.fromisoformat(iso)
    key = int(d.strftime("%Y%m%d"))
    if not conn.execute(text("SELECT 1 FROM dim_date WHERE DateKey=:k"), {"k": key}).first():
        conn.execute(text("""INSERT INTO dim_date (DateKey, FullDate, Day, Month, Quarter, Year, IsHoliday)
                             VALUES (:k, :f, :day, :m, :q, :y, 0)"""),
                     {"k": key, "f": d, "day": d.day, "m": d.month,
                      "q": (d.month - 1) // 3 + 1, "y": d.year})
    return key

# --- stored records (free-form) ---
@app.route("/api/records", methods=["GET", "POST"])
def records():
    if request.method == "GET":
        with engine.connect() as c:
            res = c.execute(text("SELECT RecordID, Title, Category, Body, CreatedAt FROM records ORDER BY RecordID DESC"))
            return jsonify([{"id": r[0], "title": r[1], "category": r[2],
                             "body": r[3], "created": str(r[4])} for r in res.fetchall()])
    data = _payload()
    title = _s(data, "title")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    with engine.begin() as c:
        r = c.execute(text("INSERT INTO records (Title, Category, Body) VALUES (:t, :cat, :b)"),
                      {"t": title[:200], "cat": (_s(data, "category") or "General")[:50],
                       "b": str(data.get("body") or "")})
        rid = r.lastrowid
    return jsonify({"id": rid, "title": title}), 201

@app.route("/api/records/<int:rid>", methods=["DELETE"])
def record_delete(rid):
    with engine.begin() as c:
        r = c.execute(text("DELETE FROM records WHERE RecordID=:id"), {"id": rid})
    if r.rowcount == 0:
        return jsonify({"error": "Record not found"}), 404
    return jsonify({"deleted": rid})

# --- lookup lists used by the forms ---
@app.route("/api/refs")
def refs():
    with engine.connect() as c:
        ps = c.execute(text("SELECT PassengerKey, FullName, FrequentFlyerTier "
                            "FROM dim_passenger WHERE IsCurrent=1 ORDER BY FullName")).fetchall()
        fs = c.execute(text("SELECT FlightKey, FlightNumber, CONCAT(OriginAirport,'-',DestinationAirport) "
                            "FROM dim_flight WHERE IsCurrent=1 ORDER BY FlightNumber")).fetchall()
        pays = c.execute(text("SELECT PaymentKey, PaymentMethod, Currency "
                              "FROM dim_payment ORDER BY PaymentKey DESC LIMIT 100")).fetchall()
    return jsonify({
        "passengers": [{"passenger_key": r[0], "name": r[1], "tier": r[2]} for r in ps],
        "flights": [{"flight_key": r[0], "flightnumber": r[1], "route": r[2]} for r in fs],
        "payments": [{"payment_key": r[0], "method": r[1], "currency": r[2]} for r in pays],
    })

# --- passengers ---
@app.route("/api/passengers", methods=["POST"])
def add_passenger():
    data = _payload()
    name = _s(data, "full_name")
    if not name:
        return jsonify({"error": "Full name is required"}), 400
    pid = "P" + datetime.datetime.now().strftime("%y%m%d%H%M%S")
    try:
        with engine.begin() as c:
            r = c.execute(text("""INSERT INTO dim_passenger
                (PassengerID, FullName, Nationality, FrequentFlyerTier, ContactInfo, EffectiveDate, IsCurrent)
                VALUES (:pid, :n, :nat, :tier, :contact, :eff, 1)"""),
                {"pid": pid, "n": name[:100], "nat": _s(data, "nationality")[:50] or None,
                 "tier": (_s(data, "tier") or "Blue")[:20], "contact": _s(data, "contact")[:150] or None,
                 "eff": datetime.date.today()})
            key = r.lastrowid
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"passenger_key": key, "name": name}), 201

# --- flights ---
@app.route("/api/flights", methods=["POST"])
def add_flight():
    data = _payload()
    num = _s(data, "flight_number").upper()
    origin = _s(data, "origin").upper()
    dest = _s(data, "destination").upper()
    if not num or len(origin) != 3 or len(dest) != 3:
        return jsonify({"error": "Flight number, origin and destination (3-letter codes) are required"}), 400
    fid = "F" + datetime.datetime.now().strftime("%y%m%d%H%M%S")
    try:
        dep = datetime.time.fromisoformat(_s(data, "departure")) if _s(data, "departure") else None
    except ValueError:
        dep = None
    try:
        eff = datetime.date.fromisoformat(_s(data, "effective_date")) if _s(data, "effective_date") else datetime.date.today()
    except ValueError:
        eff = datetime.date.today()
    try:
        with engine.begin() as c:
            r = c.execute(text("""INSERT INTO dim_flight
                (FlightID, FlightNumber, Airline, OriginAirport, DestinationAirport,
                 AircraftType, ScheduledDeparture, EffectiveDate, IsCurrent)
                VALUES (:fid, :num, :air, :o, :d, :ac, :dep, :eff, 1)"""),
                {"fid": fid, "num": num[:10], "air": _s(data, "airline")[:50] or None,
                 "o": origin, "d": dest, "ac": _s(data, "aircraft")[:30] or None,
                 "dep": dep, "eff": eff})
            key = r.lastrowid
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"flight_key": key, "flight_number": num, "route": f"{origin}-{dest}"}), 201

# --- payments ---
@app.route("/api/payments", methods=["POST"])
def add_payment():
    data = _payload()
    method = _s(data, "payment_method")
    if not method:
        return jsonify({"error": "Payment method is required"}), 400
    try:
        tdate = datetime.date.fromisoformat(_s(data, "transaction_date")) if _s(data, "transaction_date") else datetime.date.today()
    except ValueError:
        tdate = datetime.date.today()
    try:
        with engine.begin() as c:
            r = c.execute(text("""INSERT INTO dim_payment (PaymentMethod, Currency, TransactionDate, RefundStatus)
                                  VALUES (:m, :cur, :d, :ref)"""),
                {"m": method[:30], "cur": (_s(data, "currency") or "GHS").upper()[:3],
                 "d": tdate, "ref": (_s(data, "refund_status") or "None")[:20]})
            key = r.lastrowid
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"payment_key": key, "method": method}), 201

# --- bookings ---
@app.route("/api/bookings", methods=["POST"])
def add_booking():
    data = _payload()
    try:
        passenger_key = int(data.get("passenger_key"))
        flight_key = int(data.get("flight_key"))
        payment_key = int(data.get("payment_key"))
        seats = max(1, int(data.get("seats") or 1))
        fare = float(data.get("fare") or 0)
        outstanding = float(data.get("outstanding") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Passenger, flight, payment, seats and fare are required"}), 400
    status = _s(data, "status") or "Paid"
    if status not in ("Paid", "Partial", "Unpaid"):
        status = "Paid"
    bdate = _s(data, "booking_date") or datetime.date.today().isoformat()
    try:
        with engine.begin() as c:
            f = c.execute(text("SELECT OriginAirport, DestinationAirport FROM dim_flight WHERE FlightKey=:k"),
                          {"k": flight_key}).first()
            if not f:
                return jsonify({"error": "Flight not found"}), 400
            if not c.execute(text("SELECT 1 FROM dim_passenger WHERE PassengerKey=:k"),
                             {"k": passenger_key}).first():
                return jsonify({"error": "Passenger not found"}), 400
            if not c.execute(text("SELECT 1 FROM dim_payment WHERE PaymentKey=:k"),
                             {"k": payment_key}).first():
                return jsonify({"error": "Payment not found"}), 400
            date_key = _ensure_date(c, bdate)
            booking_id = "MB" + datetime.datetime.now().strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:6].upper()
            r = c.execute(text("""INSERT INTO fact_booking
                (BookingID, PassengerKey, FlightKey, BookingDateKey, PaymentKey, RouteCode,
                 SeatsBooked, FareAmountGHS, OutstandingBalanceGHS, PaymentStatus, IsRefunded, BatchID)
                VALUES (:bid, :pk, :fk, :dk, :pay, :route, :seats, :fare, :out, :status, 0, 'MANUAL')"""),
                {"bid": booking_id[:20], "pk": passenger_key, "fk": flight_key, "dk": date_key,
                 "pay": payment_key, "route": f"{f[0]}-{f[1]}", "seats": seats,
                 "fare": fare, "out": outstanding, "status": status})
            key = r.lastrowid
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"booking_key": key, "booking_id": booking_id}), 201

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)   # local only
