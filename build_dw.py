"""Builds the SkyLink star-schema facts and dashboard views.

Creates fact_booking / fact_flight_ops (plus ETL audit tables), seeds them from
the existing dimension tables, then creates the 17 vw_* views read by app.py.

Run:  python build_dw.py        (connects as MySQL root locally)
Safe to re-run: it drops and recreates only the fact/etl tables and views.
"""
import random
from datetime import date

import pymysql

random.seed(42)

CONN = dict(host="127.0.0.1", user="root", password="", database="skylink",
            charset="utf8mb4", autocommit=True)

# IATA code per airport city (dim_airport has no code column yet)
AIRPORT_CODES = {
    "Accra": "ACC",
    "London": "LHR",
    "Nairobi": "NBO",
    "Dubai": "DXB",
    "New York": "JFK",
}
BASE_FARE = {"ACC-LHR": 8500, "ACC-NBO": 4200, "ACC-DXB": 6300, "ACC-JFK": 9800}
CAPACITY = {"A330-900": 300, "A330": 280, "777": 300, "787": 250, "737": 160}

DDL = [
    "DROP VIEW IF EXISTS " + ", ".join([
        "vw_kpi_executive", "vw_revenue_by_destination", "vw_monthly_trend",
        "vw_occupancy_by_flight", "vw_delay_share", "vw_revenue_summary",
        "vw_payment_mix", "vw_customer_kpi", "vw_repeat_passengers", "vw_tier_mix",
        "vw_nationality_mix", "vw_flights_today", "vw_alerts_today",
        "vw_unpaid_imminent", "vw_etl_audit", "vw_reject_queue",
        "vw_integrity_check"]),
    "DROP TABLE IF EXISTS fact_booking",
    "DROP TABLE IF EXISTS fact_flight_ops",
    "DROP TABLE IF EXISTS etl_batch",
    "DROP TABLE IF EXISTS etl_reject",
    """
    CREATE TABLE fact_booking (
        BookingKey          INT AUTO_INCREMENT PRIMARY KEY,
        BookingID           VARCHAR(20) NOT NULL,
        PassengerKey        INT NOT NULL,
        FlightKey           INT NOT NULL,
        BookingDateKey      INT NOT NULL,
        PaymentKey          INT NOT NULL,
        RouteCode           VARCHAR(10) NOT NULL,
        SeatsBooked         INT NOT NULL,
        FareAmountGHS       DECIMAL(12,2) NOT NULL,
        OutstandingBalanceGHS DECIMAL(12,2) NOT NULL DEFAULT 0,
        PaymentStatus       VARCHAR(20) NOT NULL,
        IsRefunded          TINYINT NOT NULL DEFAULT 0,
        BatchID             VARCHAR(20) NOT NULL,
        KEY ix_fb_passenger (PassengerKey),
        KEY ix_fb_flight (FlightKey),
        KEY ix_fb_date (BookingDateKey),
        KEY ix_fb_payment (PaymentKey),
        KEY ix_fb_route (RouteCode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE fact_flight_ops (
        FlightOpKey     INT AUTO_INCREMENT PRIMARY KEY,
        FlightKey       INT NOT NULL,
        FlightDateKey   INT NOT NULL,
        RouteCode       VARCHAR(10) NOT NULL,
        Status          VARCHAR(20) NOT NULL,
        DelayMinutes    INT NOT NULL DEFAULT 0,
        SeatsSold       INT NOT NULL,
        SeatCapacity    INT NOT NULL,
        BatchID         VARCHAR(20) NOT NULL,
        KEY ix_fo_flight (FlightKey),
        KEY ix_fo_date (FlightDateKey),
        KEY ix_fo_route (RouteCode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE etl_batch (
        BatchID           VARCHAR(20) PRIMARY KEY,
        Source            VARCHAR(50) NOT NULL,
        Extracted         INT NOT NULL,
        DuplicatesRemoved INT NOT NULL,
        Rejected          INT NOT NULL,
        Loaded            INT NOT NULL,
        Unreconciled      INT NOT NULL,
        RunDate           DATE NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE etl_reject (
        RejectID  INT AUTO_INCREMENT PRIMARY KEY,
        BatchID   VARCHAR(20) NOT NULL,
        BookingID VARCHAR(20) NOT NULL,
        Reason    VARCHAR(255) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS records (
        RecordID  INT AUTO_INCREMENT PRIMARY KEY,
        Title     VARCHAR(200) NOT NULL,
        Category  VARCHAR(50) NOT NULL DEFAULT 'General',
        Body      TEXT,
        CreatedAt DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

VIEWS = {
    "vw_kpi_executive": """
        CREATE OR REPLACE VIEW vw_kpi_executive AS
        SELECT b.RouteCode AS route_code,
               b.total_passengers,
               b.total_revenue_ghs,
               o.avg_occupancy_pct,
               o.avg_delay_min,
               o.delayed_flights_pct
        FROM (SELECT RouteCode, SUM(SeatsBooked) AS total_passengers,
                     SUM(FareAmountGHS) AS total_revenue_ghs
              FROM fact_booking GROUP BY RouteCode) b
        LEFT JOIN (SELECT RouteCode,
                          ROUND(AVG(SeatsSold / SeatCapacity * 100), 2) AS avg_occupancy_pct,
                          ROUND(AVG(DelayMinutes), 2) AS avg_delay_min,
                          ROUND(SUM(CASE WHEN Status = 'Delayed' OR DelayMinutes > 15 THEN 1 ELSE 0 END)
                                / COUNT(*) * 100, 2) AS delayed_flights_pct
                   FROM fact_flight_ops GROUP BY RouteCode) o
               ON o.RouteCode = b.RouteCode
    """,
    "vw_revenue_by_destination": """
        CREATE OR REPLACE VIEW vw_revenue_by_destination AS
        SELECT b.RouteCode AS route_code,
               ap.City AS destination,
               SUM(b.FareAmountGHS) AS revenue_ghs,
               SUM(b.SeatsBooked) AS passengers
        FROM fact_booking b
        JOIN dim_flight f ON f.FlightKey = b.FlightKey
        LEFT JOIN (SELECT IATACode, MIN(City) AS City FROM dim_airport GROUP BY IATACode) ap
               ON ap.IATACode = f.DestinationAirport
        GROUP BY b.RouteCode, ap.City
    """,
    "vw_monthly_trend": """
        CREATE OR REPLACE VIEW vw_monthly_trend AS
        SELECT b.RouteCode AS route_code, d.Year AS year, d.Month AS month,
               SUM(b.SeatsBooked) AS passengers,
               SUM(b.FareAmountGHS) AS revenue_ghs
        FROM fact_booking b
        JOIN dim_date d ON d.DateKey = b.BookingDateKey
        GROUP BY b.RouteCode, d.Year, d.Month
        ORDER BY b.RouteCode, d.Year, d.Month
    """,
    "vw_occupancy_by_flight": """
        CREATE OR REPLACE VIEW vw_occupancy_by_flight AS
        SELECT o.RouteCode AS route_code, f.FlightNumber AS flightnumber,
               ROUND(AVG(o.SeatsSold / o.SeatCapacity * 100), 2) AS avg_occupancy_pct
        FROM fact_flight_ops o
        JOIN dim_flight f ON f.FlightKey = o.FlightKey
        GROUP BY o.RouteCode, f.FlightNumber
    """,
    "vw_delay_share": """
        CREATE OR REPLACE VIEW vw_delay_share AS
        SELECT o.RouteCode AS route_code, ap.City AS destination,
               SUM(CASE WHEN o.Status = 'Delayed' OR o.DelayMinutes > 15 THEN 1 ELSE 0 END) AS delayed_flights,
               ROUND(SUM(CASE WHEN o.Status = 'Delayed' OR o.DelayMinutes > 15 THEN 1 ELSE 0 END)
                     / COUNT(*) * 100, 2) AS delay_share_pct,
               ROUND(AVG(o.DelayMinutes), 2) AS avg_delay_min
        FROM fact_flight_ops o
        JOIN dim_flight f ON f.FlightKey = o.FlightKey
        LEFT JOIN (SELECT IATACode, MIN(City) AS City FROM dim_airport GROUP BY IATACode) ap
               ON ap.IATACode = f.DestinationAirport
        GROUP BY o.RouteCode, ap.City
    """,
    "vw_revenue_summary": """
        CREATE OR REPLACE VIEW vw_revenue_summary AS
        SELECT RouteCode AS route_code,
               SUM(FareAmountGHS) AS fare_revenue_ghs,
               ROUND(SUM(FareAmountGHS) / SUM(SeatsBooked), 2) AS revenue_per_passenger,
               SUM(OutstandingBalanceGHS) AS outstanding_balance_ghs,
               ROUND(SUM(OutstandingBalanceGHS) / SUM(FareAmountGHS) * 100, 2) AS outstanding_pct,
               ROUND(SUM(IsRefunded) / COUNT(*) * 100, 2) AS refund_rate_pct
        FROM fact_booking
        GROUP BY RouteCode
    """,
    "vw_payment_mix": """
        CREATE OR REPLACE VIEW vw_payment_mix AS
        SELECT b.RouteCode AS route_code, p.PaymentMethod AS paymentmethod,
               COUNT(*) AS transactions,
               ROUND(COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY b.RouteCode) * 100, 2) AS share_pct
        FROM fact_booking b
        JOIN dim_payment p ON p.PaymentKey = b.PaymentKey
        GROUP BY b.RouteCode, p.PaymentMethod
    """,
    "vw_customer_kpi": """
        CREATE OR REPLACE VIEW vw_customer_kpi AS
        SELECT (SELECT COUNT(DISTINCT PassengerKey) FROM fact_booking) AS unique_passengers,
               ROUND((SELECT AVG(SeatsBooked) FROM fact_booking), 2) AS avg_seats_per_booking,
               ROUND((SELECT COUNT(*) FROM dim_passenger
                      WHERE IsCurrent = 1 AND FrequentFlyerTier IS NOT NULL AND FrequentFlyerTier <> '')
                     / (SELECT COUNT(*) FROM dim_passenger WHERE IsCurrent = 1) * 100, 2) AS member_pct
    """,
    "vw_repeat_passengers": """
        CREATE OR REPLACE VIEW vw_repeat_passengers AS
        SELECT COUNT(*) AS passengers_total,
               ROUND(SUM(CASE WHEN c > 1 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) AS repeat_pct
        FROM (SELECT PassengerKey, COUNT(*) AS c FROM fact_booking GROUP BY PassengerKey) t
    """,
    "vw_tier_mix": """
        CREATE OR REPLACE VIEW vw_tier_mix AS
        SELECT FrequentFlyerTier AS frequentflyertier,
               ROUND(COUNT(*) / (SELECT COUNT(*) FROM dim_passenger WHERE IsCurrent = 1) * 100, 2) AS share_pct
        FROM dim_passenger
        WHERE IsCurrent = 1
        GROUP BY FrequentFlyerTier
    """,
    "vw_nationality_mix": """
        CREATE OR REPLACE VIEW vw_nationality_mix AS
        SELECT Nationality AS nationality,
               ROUND(COUNT(*) / (SELECT COUNT(*) FROM dim_passenger WHERE IsCurrent = 1) * 100, 2) AS share_pct
        FROM dim_passenger
        WHERE IsCurrent = 1
        GROUP BY Nationality
    """,
    "vw_flights_today": """
        CREATE OR REPLACE VIEW vw_flights_today AS
        SELECT o.RouteCode AS route_code, f.FlightNumber AS flightnumber,
               f.ScheduledDeparture AS departuretime,
               ap.GateNumber AS gatenumber, ap.TerminalNumber AS terminalnumber,
               o.Status AS status, o.DelayMinutes AS delay_minutes,
               o.SeatsSold AS seats_sold, o.SeatCapacity AS seat_capacity,
               ROUND(o.SeatsSold / o.SeatCapacity * 100, 2) AS occupancy_pct
        FROM fact_flight_ops o
        JOIN dim_flight f ON f.FlightKey = o.FlightKey
        LEFT JOIN dim_airport ap
               ON ap.AirportKey = (SELECT MIN(x.AirportKey) FROM dim_airport x
                                   WHERE x.IATACode = f.OriginAirport)
        WHERE o.FlightDateKey = (SELECT MAX(flightdatekey) FROM fact_flight_ops)
    """,
    "vw_alerts_today": """
        CREATE OR REPLACE VIEW vw_alerts_today AS
        SELECT o.RouteCode AS route_code, f.FlightNumber AS flightnumber,
               CASE
                   WHEN o.Status = 'Cancelled' THEN 'Flight cancelled'
                   WHEN o.Status = 'Delayed' OR o.DelayMinutes > 15
                       THEN CONCAT('Delayed ', GREATEST(o.DelayMinutes, 15), ' min')
                   WHEN o.SeatsSold / o.SeatCapacity < 0.5 THEN 'Low occupancy'
                   ELSE 'On time'
               END AS alert
        FROM fact_flight_ops o
        JOIN dim_flight f ON f.FlightKey = o.FlightKey
        WHERE o.FlightDateKey = (SELECT MAX(flightdatekey) FROM fact_flight_ops)
          AND NOT (o.Status = 'On Time' AND o.DelayMinutes <= 15
                   AND o.SeatsSold / o.SeatCapacity >= 0.5)
    """,
    "vw_unpaid_imminent": """
        CREATE OR REPLACE VIEW vw_unpaid_imminent AS
        SELECT ROUND(COALESCE(SUM(OutstandingBalanceGHS), 0), 2) AS unpaid_ghs
        FROM fact_booking
        WHERE PaymentStatus <> 'Paid' AND OutstandingBalanceGHS > 0
    """,
    "vw_etl_audit": """
        CREATE OR REPLACE VIEW vw_etl_audit AS
        SELECT BatchID AS batch_id, Source AS source, Extracted AS extracted,
               DuplicatesRemoved AS duplicates_removed, Rejected AS rejected,
               Loaded AS loaded, Unreconciled AS unreconciled
        FROM etl_batch
        ORDER BY BatchID
    """,
    "vw_reject_queue": """
        CREATE OR REPLACE VIEW vw_reject_queue AS
        SELECT BatchID AS batch_id, BookingID AS booking_id, Reason AS reason
        FROM etl_reject
        ORDER BY RejectID
    """,
    "vw_integrity_check": """
        CREATE OR REPLACE VIEW vw_integrity_check AS
        SELECT (
            (SELECT COUNT(*) FROM fact_booking b
             LEFT JOIN dim_passenger p ON p.PassengerKey = b.PassengerKey
             LEFT JOIN dim_flight f ON f.FlightKey = b.FlightKey
             LEFT JOIN dim_date d ON d.DateKey = b.BookingDateKey
             LEFT JOIN dim_payment pm ON pm.PaymentKey = b.PaymentKey
             WHERE p.PassengerKey IS NULL OR f.FlightKey IS NULL
                OR d.DateKey IS NULL OR pm.PaymentKey IS NULL)
            +
            (SELECT COUNT(*) FROM fact_flight_ops o
             LEFT JOIN dim_flight f ON f.FlightKey = o.FlightKey
             LEFT JOIN dim_date d ON d.DateKey = o.FlightDateKey
             WHERE f.FlightKey IS NULL OR d.DateKey IS NULL)
        ) AS orphan_fact_rows
    """,
}


def ensure_iata_column(cur):
    cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA='skylink' AND TABLE_NAME='dim_airport'
                     AND COLUMN_NAME='IATACode'""")
    if cur.fetchone()[0] == 0:
        cur.execute("ALTER TABLE dim_airport ADD COLUMN IATACode CHAR(3) NULL")
    for city, code in AIRPORT_CODES.items():
        cur.execute("UPDATE dim_airport SET IATACode=%s WHERE City=%s", (code, city))


def seed(cur):
    cur.execute("SELECT DateKey, FullDate FROM dim_date ORDER BY FullDate")
    dates = cur.fetchall()
    cur.execute("""SELECT FlightKey, FlightNumber, OriginAirport, DestinationAirport, AircraftType
                   FROM dim_flight WHERE IsCurrent=1""")
    flights = cur.fetchall()
    cur.execute("SELECT PassengerKey FROM dim_passenger WHERE IsCurrent=1")
    passengers = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT PaymentKey, RefundStatus FROM dim_payment")
    payments = cur.fetchall()

    max_date = max(d[1] for d in dates)
    booking_no = 0
    booking_rows, ops_rows = [], []

    for i, (datekey, fulldate) in enumerate(dates):
        batch = f"BATCH-00{i % 3 + 1}"
        for fkey, fnum, origin, dest, aircraft in flights:
            route = f"{origin}-{dest}"
            cap = next((v for k, v in CAPACITY.items() if k in aircraft), 200)

            load = random.uniform(0.55, 0.98)
            seats_sold = int(cap * load)
            if fulldate == max_date and fnum == "BA78":
                status, delay = "Delayed", 45
            elif fulldate == max_date and fnum == "KQ513":
                status, delay = "Cancelled", 0
            elif random.random() < 0.25:
                status, delay = "Delayed", random.randint(10, 70)
            else:
                status, delay = "On Time", 0
            ops_rows.append((fkey, datekey, route, status, delay, seats_sold, cap, batch))

            for _ in range(random.randint(1, len(passengers))):
                pkey = random.choice(passengers)
                seats = random.randint(1, 3)
                base = BASE_FARE.get(route, 5000)
                fare = round(base * seats * random.uniform(0.85, 1.2), 2)
                paykey, refund_status = random.choice(payments)
                roll = random.random()
                if roll < 0.70:
                    pstatus, outstanding = "Paid", 0.0
                elif roll < 0.85:
                    pstatus, outstanding = "Partial", round(fare * 0.4, 2)
                else:
                    pstatus, outstanding = "Unpaid", fare
                refunded = 1 if refund_status == "Refunded" else 0
                booking_no += 1
                booking_rows.append((
                    f"BK{booking_no:05d}", pkey, fkey, datekey, paykey, route, seats,
                    fare, outstanding, pstatus, refunded, batch))

    cur.executemany(
        """INSERT INTO fact_flight_ops
           (FlightKey, FlightDateKey, RouteCode, Status, DelayMinutes, SeatsSold, SeatCapacity, BatchID)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", ops_rows)
    cur.executemany(
        """INSERT INTO fact_booking
           (BookingID, PassengerKey, FlightKey, BookingDateKey, PaymentKey, RouteCode,
            SeatsBooked, FareAmountGHS, OutstandingBalanceGHS, PaymentStatus, IsRefunded, BatchID)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", booking_rows)

    # ETL audit + rejected rows (numbers reconcile: extracted = dup + rejected + loaded + unreconciled)
    batches = [
        ("BATCH-001", "Bookings_CSV", 120, 4, 3, 112, 1, date(2024, 1, 2)),
        ("BATCH-002", "Flights_API", 90, 2, 1, 86, 1, date(2024, 6, 3)),
        ("BATCH-003", "Payments_CSV", 150, 5, 2, 142, 1, date(2024, 12, 2)),
    ]
    cur.executemany(
        """INSERT INTO etl_batch
           (BatchID, Source, Extracted, DuplicatesRemoved, Rejected, Loaded, Unreconciled, RunDate)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", batches)
    rejects = [
        ("BATCH-001", "BK00004", "Missing passenger reference"),
        ("BATCH-001", "BK00017", "Duplicate booking id"),
        ("BATCH-002", "BK00051", "Invalid flight date key"),
        ("BATCH-003", "BK00088", "Negative fare amount"),
        ("BATCH-003", "BK00090", "Currency could not be mapped to GHS"),
    ]
    cur.executemany(
        "INSERT INTO etl_reject (BatchID, BookingID, Reason) VALUES (%s,%s,%s)", rejects)

    print(f"  Seeded {len(ops_rows)} flight operations and {len(booking_rows)} bookings.")


def main():
    conn = pymysql.connect(**CONN)
    try:
        with conn.cursor() as cur:
            print("Adding IATA codes to dim_airport...")
            ensure_iata_column(cur)
            print("Creating fact / ETL tables...")
            for stmt in DDL:
                cur.execute(stmt)
            print("Seeding facts...")
            seed(cur)
            print("Creating views...")
            for name, stmt in VIEWS.items():
                cur.execute(stmt)
                print(f"  [OK] {name}")
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
