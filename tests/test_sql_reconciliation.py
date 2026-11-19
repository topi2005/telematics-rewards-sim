"""
Check that the SQL results agree with the Python results from steps 3 and 4.

If a query is wrong (bad join, wrong denominator, double counting), the
dashboard would show wrong numbers with no error, so these tests compare
every dashboard table against an independent calculation.

Run with pytest, or directly:  python tests/test_sql_reconciliation.py
"""

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from run_sql import build_db, run_queries  # noqa: E402

N_DRIVERS, WEEKS, N_DESIGNS = 5000, 26, 4

_cache = {}


def results():
    if "res" not in _cache:
        con = build_db(ROOT / "data", ROOT / "data" / "telematics.db")
        _cache["con"] = con
        _cache["res"] = run_queries(con, ROOT / "sql")
    return _cache["res"]


def test_row_counts_and_completeness():
    res = results()
    con = _cache["con"]
    assert con.execute("SELECT COUNT(*) FROM reward_events").fetchone()[0] == N_DRIVERS * WEEKS * N_DESIGNS
    # every design has every driver in every week
    w = res["04_weekly_engagement"]
    assert (w["drivers"] == N_DRIVERS).all()
    assert len(w) == WEEKS * N_DESIGNS


def test_portfolio_bands_cover_everyone():
    res = results()["01_portfolio_score_bands"]
    claims_total = len(pd.read_csv(ROOT / "data" / "claims.csv"))
    assert res["drivers"].sum() == N_DRIVERS
    assert res["claims"].sum() == claims_total
    assert abs(res["pct_of_drivers"].sum() - 100) < 0.5


def test_deciles_match_python_validation():
    sql = results()["02_score_deciles"].set_index("decile")
    py = pd.read_csv(ROOT / "report" / "decile_table.csv").set_index("band")
    assert (sql["drivers"].values == py["drivers"].values).all()
    assert (sql["claims"].values == py["claims"].values).all()
    assert np.allclose(sql["claims_per_driver_year"].values, py["frequency"].values, atol=1e-3)


def test_segments_cover_everyone_twice():
    seg = results()["03_segment_check"]
    for kind in ["Age band", "Region"]:
        assert seg.loc[seg["segment_type"] == kind, "drivers"].sum() == N_DRIVERS


def test_weekly_metrics_match_python_simulation():
    sql = results()["04_weekly_engagement"].sort_values(["design", "week"]).reset_index(drop=True)
    py = pd.read_csv(ROOT / "report" / "weekly_design_metrics.csv").sort_values(["design", "week"]).reset_index(drop=True)
    for col in ["effort_rate", "goal_hit_rate", "share_rewarded"]:
        assert np.allclose(sql[col], py[col], atol=1e-3), col
    assert np.allclose(sql["mean_observed_score"], py["mean_observed_score"], atol=0.02)
    assert np.allclose(sql["reward_cost_total"], py["reward_cost_total"], atol=2.0)


def test_control_pays_nothing_and_streaks_only_where_defined():
    w = results()["04_weekly_engagement"]
    ctrl = w[w["design"].str.startswith("Control")]
    assert (ctrl["reward_cost_total"] == 0).all()
    assert ctrl["avg_streak_length"].isna().all()
    assert w[w["design"].str.startswith("B:")]["avg_streak_length"].notna().all()


def test_distribution_shares_sum_to_one():
    res = results()
    for name in ["05_streak_distribution", "06_tier_distribution"]:
        sums = res[name].groupby("week")["share_of_drivers"].sum()
        assert np.allclose(sums, 1.0, atol=1e-3), name


def test_economics_match_python_simulation():
    sql = results()["07_design_economics"].set_index("design")
    py = pd.read_csv(ROOT / "report" / "reward_design_summary.csv").set_index("design")
    pairs = {
        "reward_cost_per_driver": 0.1,
        "claims_saving_per_driver": 0.1,
        "net_per_driver": 0.1,
        "avg_score_uplift_pts": 0.02,
    }
    for col, tol in pairs.items():
        pycol = "avg_score_uplift_pts" if col == "avg_score_uplift_pts" else col
        assert np.allclose(sql.loc[py.index, col], py[pycol], atol=tol), col
    assert np.allclose(sql.loc[py.index, "saving_per_R1_reward"], py["saving_per_R1_reward"], atol=0.01)


def test_thirds_reconcile_with_overall():
    res = results()
    third = res["08_economics_by_baseline_third"]
    overall = res["07_design_economics"].set_index("design")
    for design, g in third.groupby("design"):
        weighted = np.average(g["reward_cost_per_driver"], weights=g["drivers"])
        assert abs(weighted - overall.loc[design, "reward_cost_per_driver"]) < 0.05, design


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)
