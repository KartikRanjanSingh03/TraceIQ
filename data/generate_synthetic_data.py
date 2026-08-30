"""
data/generate_synthetic_data.py — Synthetic MVP dataset generator for TraceIQ.

Generates all four source tables per 05_DATA_SCHEMA.md, implementing:
  • The MVP scenario: Revenue ↓8.2% driven by North-region Electronics order decline
    caused by a warehouse inventory outage (01_PRD.md §6)
  • Sparse-history SKU: "Electronics - Model X" in North, launched 18 days before
    analysis date (01_PRD.md §6.2)
  • Deliberately ambiguous competitor signal that should remain "Insufficient Evidence"
    (01_PRD.md §6, 08_CONFIDENCE_AND_ABSTENTION.md §5)
  • ~120 days of history so 90-day rolling baselines work (07_ANALYTICS doc §2.1)

Fixed random seed = 42 for full reproducibility across runs (15_TESTING_STRATEGY.md §6).

Usage:
    python -m data.generate_synthetic_data
    # or with args:
    python -m data.generate_synthetic_data --analysis-date 2026-08-29 --load-db
"""
from __future__ import annotations

import argparse
import random
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────

SEED = 42
rng = np.random.default_rng(SEED)
random.seed(SEED)

ANALYSIS_DATE = date(2026, 8, 29)          # T-0 for the injected anomaly
HISTORY_START = ANALYSIS_DATE - timedelta(days=119)   # 120 days total
HISTORY_END   = ANALYSIS_DATE

REGIONS    = ["North", "South", "East", "West"]
CATEGORIES = ["Electronics", "Apparel", "Home"]
CHANNELS   = ["Online", "Marketplace", "In-store"]
SEGMENTS   = ["New", "Returning", "Premium"]
WAREHOUSES = {
    "North": "WH-N01", "South": "WH-S01",
    "East":  "WH-E01", "West":  "WH-W01",
}

# Sparse-history SKU: launched 18 days before analysis date
SPARSE_SKU_ID     = "ELEC-MODELX-001"
SPARSE_SKU_LAUNCH = ANALYSIS_DATE - timedelta(days=18)

# Outage window: 5 days including and before analysis date
OUTAGE_START = ANALYSIS_DATE - timedelta(days=4)
OUTAGE_END   = ANALYSIS_DATE

# Base daily order volumes per (region, category) — used for baseline generation
BASE_ORDERS: dict[tuple[str, str], int] = {
    ("North", "Electronics"): 420,
    ("North", "Apparel"):      310,
    ("North", "Home"):         260,
    ("South", "Electronics"): 380,
    ("South", "Apparel"):      290,
    ("South", "Home"):         230,
    ("East",  "Electronics"): 340,
    ("East",  "Apparel"):      270,
    ("East",  "Home"):         200,
    ("West",  "Electronics"): 300,
    ("West",  "Apparel"):      240,
    ("West",  "Home"):         180,
}

# Typical SKUs per category (subset used in generation)
SKUS: dict[str, list[str]] = {
    "Electronics": ["ELEC-001", "ELEC-002", "ELEC-003", SPARSE_SKU_ID],
    "Apparel":     ["APPL-001", "APPL-002", "APPL-003"],
    "Home":        ["HOME-001", "HOME-002", "HOME-003"],
}

# Base prices per category
BASE_PRICES: dict[str, float] = {
    "Electronics": 12000.0,
    "Apparel":     1800.0,
    "Home":        3500.0,
}


# ── Helpers ────────────────────────────────────────────────────────────────

def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def is_outage_day(d: date, region: str, category: str) -> bool:
    """True on days when the North Electronics warehouse outage is active."""
    return (
        region == "North"
        and category == "Electronics"
        and OUTAGE_START <= d <= OUTAGE_END
    )


def dow_factor(d: date) -> float:
    """Weekday multiplier (weekends ~20% lower demand)."""
    return 0.80 if d.weekday() >= 5 else 1.0


def noise(scale: float = 0.05) -> float:
    return float(rng.normal(0, scale))


# ── Table generators ───────────────────────────────────────────────────────

def generate_orders() -> pd.DataFrame:
    """
    Generate raw.orders rows.

    Anomaly injection:
    - Days in OUTAGE_START..OUTAGE_END for North / Electronics: order volume drops ~72%
      (stockout → customers can't order; stock confirmed missing by inventory table)
    - All other regions / categories: normal seasonal variation, no anomaly
    - Sparse SKU (ELEC-MODELX-001): only present from SPARSE_SKU_LAUNCH onward, in North only
    - Competitor signal: one extra dip in South/Electronics during outage window (weak, not
      matching the North-specific pattern → should land as Insufficient Evidence)
    """
    rows: list[dict] = []

    for d in daterange(HISTORY_START, HISTORY_END):
        for region in REGIONS:
            for category in CATEGORIES:
                base = BASE_ORDERS[(region, category)]
                skus = SKUS[category].copy()

                # Sparse SKU: only available in North from launch date
                if category == "Electronics":
                    if region != "North" or d < SPARSE_SKU_LAUNCH:
                        skus = [s for s in skus if s != SPARSE_SKU_ID]

                factor = dow_factor(d) * (1 + noise(0.06))

                # ── Outage injection (primary, deliberate) ──────────────
                if is_outage_day(d, region, category):
                    factor *= 0.28   # 72% drop → ~8.2% total revenue impact

                # ── Competitor signal (secondary, weak, ambiguous) ──────
                # Small dip in South/Electronics during same window, but NOT matching
                # North's segment pattern → deliberately insufficient evidence
                if (region == "South" and category == "Electronics"
                        and OUTAGE_START <= d <= OUTAGE_END):
                    factor *= 0.94   # only ~6% dip, not statistically significant alone

                n_orders = max(1, int(base * factor))

                for _ in range(n_orders):
                    sku = random.choice(skus)
                    channel = random.choice(CHANNELS)
                    segment = random.choice(SEGMENTS)
                    price = BASE_PRICES[category] * (1 + noise(0.08))
                    discount = max(0, min(0.35, float(rng.normal(0.10, 0.04))))
                    units = random.choices([1, 2, 3], weights=[0.75, 0.20, 0.05])[0]
                    revenue = round(units * price * (1 - discount), 2)

                    # product_launch_date: sparse SKU gets its real launch date; others get old date
                    if sku == SPARSE_SKU_ID:
                        launch = SPARSE_SKU_LAUNCH
                    else:
                        launch = HISTORY_START - timedelta(days=180)  # well-established

                    rows.append({
                        "order_line_id":      str(uuid.uuid4()),
                        "order_id":           f"ORD-{d.strftime('%Y%m%d')}-{region[:1]}{category[:1]}-"
                                              f"{rng.integers(10000, 99999)}",
                        "date":               d,
                        "region":             region,
                        "category":           category,
                        "product_id":         sku,
                        "channel":            channel,
                        "customer_segment":   segment,
                        "units":              units,
                        "price":              round(price, 2),
                        "discount_pct":       round(discount, 4),
                        "revenue":            revenue,
                        "product_launch_date": launch,
                    })

    return pd.DataFrame(rows)


def generate_inventory_snapshots() -> pd.DataFrame:
    rows: list[dict] = []

    # Determine a baseline set of SKUs per (region, category)
    region_cat_skus: dict[tuple[str, str], list[str]] = {}
    for region in REGIONS:
        for category in CATEGORIES:
            base_skus = [s for s in SKUS[category] if s != SPARSE_SKU_ID]
            if region == "North" and category == "Electronics":
                # Sparse SKU appears in North inventory from launch date
                all_skus = base_skus + [SPARSE_SKU_ID]
            else:
                all_skus = base_skus
            region_cat_skus[(region, category)] = all_skus

    for d in daterange(HISTORY_START, HISTORY_END):
        for region in REGIONS:
            for category in CATEGORIES:
                skus = region_cat_skus[(region, category)]
                for sku in skus:
                    # Sparse SKU: no snapshot before launch
                    if sku == SPARSE_SKU_ID and d < SPARSE_SKU_LAUNCH:
                        continue

                    # Outage: North Electronics stock = 0 during outage window
                    if is_outage_day(d, region, category):
                        stock = 0
                        stockout = True
                    else:
                        # Normal stock with day-to-day variation
                        stock = max(0, int(rng.normal(500, 80)))
                        stockout = stock == 0

                    rows.append({
                        "snapshot_id":                str(uuid.uuid4()),
                        "date":                       d,
                        "region":                     region,
                        "category":                   category,
                        "product_id":                 sku,
                        "warehouse_id":               WAREHOUSES[region],
                        "stock_on_hand":              stock,
                        "stockout_flag":              stockout,
                        "replenishment_lead_time_days": random.choice([2, 3, 4, 5]),
                        "data_quality_flag":          None,
                    })

    return pd.DataFrame(rows)


def generate_deliveries() -> pd.DataFrame:
    rows: list[dict] = []

    for d in daterange(HISTORY_START, HISTORY_END):
        for region in REGIONS:
            # ~100–150 deliveries per region per day
            n_del = int(rng.normal(125, 15))
            for _ in range(n_del):
                dispatch_ts = datetime(d.year, d.month, d.day,
                                       random.randint(8, 20), random.randint(0, 59))
                promised_sla = random.choice([24, 48, 72])

                # SLA breach rate slightly elevated during outage (logistics stress)
                breach_prob = 0.08
                if region == "North" and OUTAGE_START <= d <= OUTAGE_END:
                    breach_prob = 0.18   # elevated but not extreme — SLA is a secondary signal

                sla_breach = rng.random() < breach_prob
                actual_hrs = round(float(rng.normal(promised_sla * 0.85, 8)), 1)
                if sla_breach:
                    actual_hrs = round(float(rng.normal(promised_sla * 1.3, 10)), 1)

                # ~10% in-transit (not yet delivered at snapshot time)
                in_transit = rng.random() < 0.10
                delivered_ts = None if in_transit else (
                    dispatch_ts + timedelta(hours=actual_hrs)
                )

                rows.append({
                    "delivery_id":           str(uuid.uuid4()),
                    "order_id":              f"ORD-{d.strftime('%Y%m%d')}-{region[:1]}E-"
                                             f"{rng.integers(10000, 99999)}",
                    "dispatch_timestamp":    dispatch_ts,
                    "delivered_timestamp":   delivered_ts,
                    "region":                region,
                    "promised_sla_hours":    promised_sla,
                    "actual_delivery_hours": None if in_transit else actual_hrs,
                    "sla_breach_flag":       False if in_transit else sla_breach,
                    "data_quality_flag":     "in_transit" if in_transit else None,
                })

    return pd.DataFrame(rows)


def generate_tickets_notes() -> pd.DataFrame:
    """
    Generate support tickets / ops reports / market notes.

    Planted documents (ground truth for RAG retrieval test, 15_TESTING_STRATEGY.md §4.6):
    1. Warehouse outage report (North, Electronics, during outage) → strong evidence
    2. Replenishment delay notice → corroborating ops evidence
    3. Competitor promotion mention (South region, vague) → deliberately weak, should not
       disambiguate as primary cause for North Electronics drop (Insufficient Evidence)
    4. Normal noise tickets spread across the date range
    """
    rows: list[dict] = []

    # ── Planted: warehouse outage report ─────────────────────────────────
    rows.append({
        "doc_id":     "PLANTED-OUTAGE-001",
        "timestamp":  datetime(OUTAGE_START.year, OUTAGE_START.month, OUTAGE_START.day, 7, 30),
        "region":     "North",
        "category":   "Electronics",
        "product_id": None,
        "source":     "ops_report",
        "text": (
            "URGENT: WH-N01 warehouse experienced a critical inventory management system failure "
            "starting 2026-08-25. All Electronics SKUs in the North warehouse are currently "
            "showing zero available stock due to the system outage. Dispatch operations for "
            "Electronics category from North warehouse are suspended until further notice. "
            "Estimated resolution: 3-5 business days. Replenishment orders have been placed "
            "but will take 48-72 hours to arrive and be processed."
        ),
    })

    # ── Planted: replenishment delay corroboration ────────────────────────
    rows.append({
        "doc_id":     "PLANTED-REPLEN-001",
        "timestamp":  datetime(OUTAGE_START.year, OUTAGE_START.month, OUTAGE_START.day + 1, 10, 0),
        "region":     "North",
        "category":   "Electronics",
        "product_id": None,
        "source":     "support_ticket",
        "text": (
            "Multiple customer complaints received: Electronics items out of stock in North region. "
            "Orders being cancelled or shifted to other regions. Replenishment team confirms "
            "stock cannot be replenished before 2026-09-02 due to supplier delays. "
            "Estimated order loss: 300-400 orders per day during the outage window."
        ),
    })

    # ── Planted: competitor signal (deliberately weak / ambiguous) ────────
    # Note: mentions South region, not North — does NOT match the primary anomaly segment.
    # This is the competitor-activity hypothesis that should land as Insufficient Evidence.
    rows.append({
        "doc_id":     "PLANTED-COMPETITOR-001",
        "timestamp":  datetime(OUTAGE_START.year, OUTAGE_START.month, OUTAGE_START.day + 2, 15, 0),
        "region":     "South",
        "category":   None,
        "product_id": None,
        "source":     "market_note",
        "text": (
            "Market intelligence note: A competitor has launched a promotional campaign "
            "this week offering 20% discount on electronics products. The promotion appears "
            "to be targeted primarily at southern markets. Some sales team members in the "
            "South region have noted slightly lower order volumes but no material impact "
            "has been confirmed. North region appears unaffected by this campaign."
        ),
    })

    # ── Noise: normal operational tickets across the date range ──────────
    noise_templates = [
        ("support_ticket", "Customer complaint about delivery delay. Ticket resolved within SLA."),
        ("ops_report",     "Routine daily inventory reconciliation completed. No anomalies detected."),
        ("support_ticket", "Order modification request processed successfully."),
        ("market_note",    "Weekly market summary: category performance within expected range."),
        ("support_ticket", "Refund processed for damaged item. Quality team notified."),
        ("ops_report",     "Warehouse capacity utilization at 78%. Operations normal."),
    ]

    for i in range(80):
        d_offset = random.randint(0, 110)
        d = HISTORY_START + timedelta(days=d_offset)
        ts = datetime(d.year, d.month, d.day, random.randint(8, 18), random.randint(0, 59))
        source, text = random.choice(noise_templates)
        rows.append({
            "doc_id":     f"NOISE-{i:04d}",
            "timestamp":  ts,
            "region":     random.choice(REGIONS + [None, None]),   # ~1/3 untagged
            "category":   random.choice(CATEGORIES + [None, None]),
            "product_id": None,
            "source":     source,
            "text":       text,
        })

    return pd.DataFrame(rows)


# ── DB loader ──────────────────────────────────────────────────────────────

def load_to_db(
    orders: pd.DataFrame,
    inventory: pd.DataFrame,
    deliveries: pd.DataFrame,
    tickets: pd.DataFrame,
) -> None:
    """Load all four DataFrames into the raw.* tables via SQLAlchemy."""
    import os
    from sqlalchemy import create_engine

    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url, echo=False)

    print("Loading raw.orders …", end="  ")
    orders.to_sql("orders", engine, schema="raw", if_exists="append", index=False,
                  method="multi", chunksize=500)
    print(f"{len(orders):,} rows")

    print("Loading raw.inventory_snapshots …", end="  ")
    inventory.to_sql("inventory_snapshots", engine, schema="raw", if_exists="append",
                     index=False, method="multi", chunksize=500)
    print(f"{len(inventory):,} rows")

    print("Loading raw.deliveries …", end="  ")
    deliveries.to_sql("deliveries", engine, schema="raw", if_exists="append",
                      index=False, method="multi", chunksize=500)
    print(f"{len(deliveries):,} rows")

    print("Loading raw.tickets_notes …", end="  ")
    tickets.to_sql("tickets_notes", engine, schema="raw", if_exists="append",
                   index=False, method="multi", chunksize=500)
    print(f"{len(tickets):,} rows")

    print("All data loaded OK.")


# ── CSV saver ──────────────────────────────────────────────────────────────

def save_csvs(
    orders: pd.DataFrame,
    inventory: pd.DataFrame,
    deliveries: pd.DataFrame,
    tickets: pd.DataFrame,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    orders.to_csv(out_dir / "orders.csv", index=False)
    inventory.to_csv(out_dir / "inventory_snapshots.csv", index=False)
    deliveries.to_csv(out_dir / "deliveries.csv", index=False)
    tickets.to_csv(out_dir / "tickets_notes.csv", index=False)
    print(f"CSVs saved to {out_dir}/")


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate TraceIQ synthetic MVP dataset")
    parser.add_argument("--load-db",  action="store_true", help="Load data into Postgres")
    parser.add_argument("--save-csv", action="store_true", default=True,
                        help="Save CSVs to data/seed/ (default: True)")
    parser.add_argument("--analysis-date", default=str(ANALYSIS_DATE),
                        help="Analysis date (YYYY-MM-DD)")
    args = parser.parse_args()

    print("Generating synthetic data (seed=42) …")
    orders     = generate_orders()
    inventory  = generate_inventory_snapshots()
    deliveries = generate_deliveries()
    tickets    = generate_tickets_notes()

    print(f"  orders:               {len(orders):>8,} rows")
    print(f"  inventory_snapshots:  {len(inventory):>8,} rows")
    print(f"  deliveries:           {len(deliveries):>8,} rows")
    print(f"  tickets_notes:        {len(tickets):>8,} rows")

    if args.save_csv:
        seed_dir = Path(__file__).parent / "seed"
        save_csvs(orders, inventory, deliveries, tickets, seed_dir)

    if args.load_db:
        load_to_db(orders, inventory, deliveries, tickets)


if __name__ == "__main__":
    main()
