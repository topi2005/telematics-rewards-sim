"""
Explainable 0-100 driver score built ONLY from trip behaviour.

    penalty_i = weight_i * clip((rate_i - floor_i) / (cap_i - floor_i), 0, 1)
    score     = 100 - sum(penalty_i)

Each component converts a behaviour rate into a penalty between 0 and its
weight. Rates at or below the floor cost nothing (normal safe driving);
rates at or above the cap take the full penalty. A higher score means
safer driving.

This module deliberately reads only trips.csv. It never touches
`latent_risk` or claims, which keeps the validation in step 3 honest.

Weights and caps are ASSUMPTIONS:
  * weights are a starting point based on how insurers commonly think about
    risk drivers (speeding and harsh braking first); step 3 compares them
    with a Poisson regression fitted on claims.
  * floors sit near the 10th percentile and caps near the 95th percentile
    of the synthetic population, rounded. A driver at the cap is in the
    worst ~5% on that behaviour. Without floors, the median score was 58
    and only 2.7% of drivers reached 80, which is a poor base for goals.

Usage:
    python src/score.py --trips data/trips.csv --out data/drivers_scored.csv
"""

import argparse
from pathlib import Path

import pandas as pd

# component: (weight, floor, cap, human-readable unit)
COMPONENTS = {
    "speeding": (30, 3.0, 20.0, "% of trip time speeding (distance-weighted)"),
    "harsh_braking": (25, 3.0, 16.0, "harsh brakes per 100 km"),
    "harsh_accel_corner": (15, 4.0, 15.0, "harsh accelerations + corners per 100 km"),
    "night_driving": (15, 0.03, 0.30, "share of km driven 21:00-05:00"),
    "phone_use": (15, 100.0, 900.0, "seconds of phone use per 100 km"),
}

assert sum(c[0] for c in COMPONENTS.values()) == 100, "weights must sum to 100"


def driver_rates(trips: pd.DataFrame) -> pd.DataFrame:
    """Aggregate trip rows into one row of behaviour rates per driver."""
    t = trips.copy()
    is_night = (t["start_hour"] >= 21) | (t["start_hour"] < 5)
    t["speeding_km"] = t["pct_time_speeding"] * t["distance_km"]
    t["night_km"] = t["distance_km"] * is_night

    g = t.groupby("driver_id").agg(
        km=("distance_km", "sum"),
        trips=("trip_id", "count"),
        speeding_km=("speeding_km", "sum"),
        brakes=("harsh_brakes", "sum"),
        accels=("harsh_accels", "sum"),
        corners=("harsh_corners", "sum"),
        phone_sec=("phone_use_sec", "sum"),
        night_km=("night_km", "sum"),
    )
    return pd.DataFrame(
        {
            "km": g["km"],
            "trips": g["trips"],
            "speeding": g["speeding_km"] / g["km"],
            "harsh_braking": g["brakes"] / g["km"] * 100,
            "harsh_accel_corner": (g["accels"] + g["corners"]) / g["km"] * 100,
            "night_driving": g["night_km"] / g["km"],
            "phone_use": g["phone_sec"] / g["km"] * 100,
        }
    )


def score_from_rates(rates: pd.DataFrame) -> pd.DataFrame:
    """Return rates plus a penalty column per component and the final score."""
    out = rates.copy()
    for name, (weight, floor, cap, _) in COMPONENTS.items():
        scaled = (out[name] - floor) / (cap - floor)
        out[f"penalty_{name}"] = weight * scaled.clip(lower=0.0, upper=1.0)
    penalty_cols = [f"penalty_{n}" for n in COMPONENTS]
    out["score"] = 100 - out[penalty_cols].sum(axis=1)
    return out


def compute_scores(trips: pd.DataFrame) -> pd.DataFrame:
    """Convenience wrapper: trips in, scored drivers out (reusable in step 4)."""
    return score_from_rates(driver_rates(trips))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trips", default="data/trips.csv")
    p.add_argument("--out", default="data/drivers_scored.csv")
    args = p.parse_args()

    trips = pd.read_csv(args.trips)
    assert "latent_risk" not in trips.columns, "score must not see hidden fields"

    scored = compute_scores(trips).round(3)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    scored.reset_index().to_csv(args.out, index=False)
    print(f"Wrote {len(scored):,} scored drivers to {args.out}")

    s = scored["score"]
    print("\nScore distribution:")
    print(s.describe(percentiles=[0.05, 0.25, 0.5, 0.75, 0.95]).round(1).to_string())
    print("\nAverage penalty by component (max possible in brackets):")
    for name, (weight, *_) in COMPONENTS.items():
        print(f"  {name:20s} {scored[f'penalty_{name}'].mean():5.1f}  ({weight})")
    print("\nShare of drivers by score band:")
    bands = pd.cut(s, [0, 50, 65, 80, 90, 100], labels=["<50", "50-65", "65-80", "80-90", "90+"])
    print((bands.value_counts(normalize=True).sort_index() * 100).round(1).astype(str).add("%").to_string())


if __name__ == "__main__":
    main()
