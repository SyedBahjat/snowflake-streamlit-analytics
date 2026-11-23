"""Incremental ELT: copy rows changed since the last sync from the CRM DB into the warehouse.

Watermark = MAX(updated_at) already in the warehouse. We pull updated_at >= watermark (>= so rows written
in the same second as the last sync aren't missed) and MERGE on the primary key, so re-runs are idempotent.

    python sync.py                    # Snowflake (default connection in ~/.snowflake/connections.toml)
    WAREHOUSE=duckdb python sync.py   # local DuckDB
"""

import sqlite3
import time
from pathlib import Path

import pandas as pd

from seed import SOURCE_DB, TS
from warehouse import Warehouse

TABLES = {"agents": "agent_id", "leads": "lead_id", "calls": "call_id"}


def extract_changes(src: sqlite3.Connection, table: str, watermark: pd.Timestamp | None) -> pd.DataFrame:
    if pd.isna(watermark):
        df = pd.read_sql(f"SELECT * FROM {table}", src)
    else:
        df = pd.read_sql(f"SELECT * FROM {table} WHERE updated_at >= ?", src, params=(watermark.strftime(TS),))
    for col in df.columns:
        if col.endswith("_at"):
            df[col] = pd.to_datetime(df[col], format=TS)
        elif col.endswith("_id") or col in ("bedrooms", "budget", "funnel_step", "duration_sec"):
            df[col] = df[col].astype("Int64")  # nullable ints (AI calls have no agent_id)
    return df


def sync(wh: Warehouse, source: Path = SOURCE_DB) -> dict[str, int]:
    if not source.exists():
        raise FileNotFoundError(f"{source} not found - run `python seed.py` first")
    wh.ensure_schema()
    src = sqlite3.connect(source)
    moved = {}
    for table, key in TABLES.items():
        watermark = wh.query(f"SELECT MAX(updated_at) AS wm FROM {table}")["wm"].iloc[0]
        started = time.perf_counter()
        df = extract_changes(src, table, None if pd.isna(watermark) else pd.Timestamp(watermark))
        if not df.empty:
            wh.upsert(table, df, key)
        moved[table] = len(df)
        print(f"{table:<7} watermark={watermark}  merged {len(df):>7,} rows in {time.perf_counter() - started:.1f}s")
    return moved


if __name__ == "__main__":
    wh = Warehouse()
    print(f"Target: {wh.describe()}")
    try:
        sync(wh)
    finally:
        wh.close()
