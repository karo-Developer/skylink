"""Checks the link between app.py, your database and the dashboard.
Run:  python check_db.py
"""
from sqlalchemy import text
from app import engine, VIEWS, DB_URL

# Columns the dashboard page reads from each view (lower-case)
EXPECT = {
 "vw_kpi_executive": "route_code,total_passengers,total_revenue_ghs,avg_occupancy_pct,avg_delay_min,delayed_flights_pct",
 "vw_revenue_by_destination": "route_code,destination,revenue_ghs,passengers",
 "vw_monthly_trend": "route_code,year,month,passengers,revenue_ghs",
 "vw_occupancy_by_flight": "route_code,flightnumber,avg_occupancy_pct",
 "vw_delay_share": "route_code,destination,delayed_flights,delay_share_pct,avg_delay_min",
 "vw_revenue_summary": "route_code,fare_revenue_ghs,revenue_per_passenger,outstanding_balance_ghs,outstanding_pct,refund_rate_pct",
 "vw_payment_mix": "route_code,paymentmethod,transactions,share_pct",
 "vw_customer_kpi": "unique_passengers,avg_seats_per_booking,member_pct",
 "vw_repeat_passengers": "passengers_total,repeat_pct",
 "vw_tier_mix": "frequentflyertier,share_pct",
 "vw_nationality_mix": "nationality,share_pct",
 "vw_flights_today": "route_code,flightnumber,departuretime,gatenumber,terminalnumber,status,delay_minutes,seats_sold,seat_capacity,occupancy_pct",
 "vw_alerts_today": "route_code,flightnumber,alert",
 "vw_unpaid_imminent": "unpaid_ghs",
 "vw_etl_audit": "batch_id,source,extracted,duplicates_removed,rejected,loaded,unreconciled",
 "vw_reject_queue": "batch_id,booking_id,reason",
 "vw_integrity_check": "orphan_fact_rows",
}

print("Step 1: connection")
print("  Connecting to:", DB_URL.split("@")[-1])   # hides the password
try:
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    print("  OK\n")
except Exception as e:
    print("  FAILED:", e)
    print("  Fix DATABASE_URL (or DB_URL in app.py) and the database driver, then run again.")
    raise SystemExit(1)

print("Step 2 and 3: views and columns")
ok = 0
for v in VIEWS:
    try:
        with engine.connect() as c:
            res = c.execute(text(f"SELECT * FROM {v}"))
            cols = [k.lower() for k in res.keys()]
            n = len(res.fetchmany(1))
        missing = [x for x in EXPECT[v].split(",") if x not in cols]
        if missing:
            print(f"  [COLUMNS] {v}: missing {missing}. Found: {cols}")
        else:
            ok += 1
            print(f"  [OK]      {v}" + ("" if n else "  (works, but returns no rows yet)"))
    except Exception as e:
        print(f"  [MISSING] {v}: {str(e).splitlines()[0][:110]}")
print(f"\n{ok} of {len(VIEWS)} views are ready.")