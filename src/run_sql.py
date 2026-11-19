"""
Step 5: load the simulation outputs into SQLite, run every query in sql/, and
export the results as CSVs for Power BI.

    data/*.csv  ->  data/telematics.db  ->  sql/*.sql  ->  dashboard/data/*.csv

Power BI can import the exported CSVs directly (Get data > Text/CSV), so no
database connection is needed on the dashboard side.

Usage:
    python src/run_sql.py --data data --sql sql --out dashboard/data
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

TABLES = {
    "drivers": "drivers.csv",
    "drivers_scored": "drivers_scored.csv",
    "claims": "claims.csv",
    "reward_events": "reward_events.csv",
    "driver_design_outcomes": "driver_design_outcomes.csv",
}
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_events_design_week ON reward_events (design, week)",
    "CREATE INDEX IF NOT EXISTS ix_events_driver ON reward_events (driver_id)",
    "CREATE INDEX IF NOT EXISTS ix_outcomes_design ON driver_design_outcomes (design)",
    "CREATE INDEX IF NOT EXISTS ix_claims_driver ON claims (driver_id)",
]


def build_db(data_dir: Path, db_path: Path) -> sqlite3.Connection:
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    for table, filename in TABLES.items():
        df = pd.read_csv(data_dir / filename, low_memory=False)
        df.to_sql(table, con, index=False, chunksize=50_000)
    for stmt in INDEXES:
        con.execute(stmt)
    con.commit()
    return con


def run_queries(con: sqlite3.Connection, sql_dir: Path) -> dict:
    results = {}
    for path in sorted(sql_dir.glob("*.sql")):
        results[path.stem] = pd.read_sql_query(path.read_text(), con)
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data")
    p.add_argument("--sql", default="sql")
    p.add_argument("--out", default="dashboard/data")
    args = p.parse_args()

    data_dir, out_dir = Path(args.data), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = build_db(data_dir, data_dir / "telematics.db")
    print(f"Built {data_dir / 'telematics.db'}")
    for table in TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:26s} {n:>9,} rows")

    print()
    for name, df in run_queries(con, Path(args.sql)).items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
        print(f"{name}.csv: {len(df)} rows x {len(df.columns)} columns")
    print(f"\nExports written to {out_dir.resolve()}")


if __name__ == "__main__":
    main()
