"""
Generate synthetic telematics data for the driver scoring and rewards simulation.

Design principle (avoids circularity):
  * Each driver has a HIDDEN `latent_risk`. Trip behaviour AND claims are both
    generated from it, but independently of each other.
  * The driver score (step 2) only ever sees trip behaviour, never `latent_risk`
    or claims. Validation (step 3) then tests whether the score recovers the
    claims pattern, which is a fair test because it was not built from it.
  * Each driver also has behaviour "quirks" unrelated to risk, so the score is
    a noisy proxy for risk, as it would be with real data.

All parameters are ASSUMPTIONS, not real-world estimates. The base claim
frequency is set high enough to give a usable number of claims for validation.

Usage:
    python src/generate_data.py --drivers 5000 --weeks 26 --seed 42 --out data
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

AGE_BANDS = ["18-24", "25-34", "35-49", "50-64", "65+"]
AGE_P = [0.10, 0.28, 0.32, 0.22, 0.08]
AGE_RISK = {"18-24": 0.6, "25-34": 0.2, "35-49": -0.1, "50-64": -0.2, "65+": 0.1}

REGIONS = ["urban", "suburban", "rural"]
REGION_P = [0.50, 0.35, 0.15]
REGION_RISK = {"urban": 0.15, "suburban": 0.0, "rural": -0.1}
REGION_DIST = {"urban": 0.8, "suburban": 1.0, "rural": 1.4}  # trip length multiplier

NIGHT_HOURS = np.array([21, 22, 23, 0, 1, 2, 3, 4])
DAY_HOURS = np.arange(5, 21)
DAY_WEIGHTS = np.array([1, 3, 8, 9, 4, 3, 3, 4, 3, 3, 4, 7, 9, 6, 3, 2], dtype=float)
DAY_WEIGHTS /= DAY_WEIGHTS.sum()

BASE_ANNUAL_CLAIM_FREQ = 0.15  # assumption, tuned for enough claims to validate
RISK_CLAIM_EFFECT = 0.6        # claim frequency multiplies by exp(0.6 * risk)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def make_drivers(rng, n):
    age = rng.choice(AGE_BANDS, size=n, p=AGE_P)
    region = rng.choice(REGIONS, size=n, p=REGION_P)
    latent_risk = (
        np.array([AGE_RISK[a] for a in age])
        + np.array([REGION_RISK[r] for r in region])
        + rng.normal(0, 0.9, n)
    )
    return pd.DataFrame(
        {
            "driver_id": [f"D{i + 1:05d}" for i in range(n)],
            "age_band": age,
            "region": region,
            "vehicle_age": rng.integers(0, 16, n),
            "latent_risk": latent_risk.round(4),
            "engagement_propensity": rng.beta(2, 3, n).round(4),
            # internal only (dropped before saving): risk-independent quirks
            "_speed_quirk": rng.normal(0, 0.5, n),
            "_brake_quirk": rng.normal(0, 0.5, n),
            "_accel_quirk": rng.normal(0, 0.5, n),
            "_corner_quirk": rng.normal(0, 0.5, n),
            "_phone_quirk": rng.normal(0, 0.5, n),
            "_night_quirk": rng.normal(0, 0.5, n),
            "_activity": rng.lognormal(0, 0.3, n),
        }
    )


def make_trips(rng, drivers, weeks, start_date, trips_per_week):
    n = len(drivers)
    n_trips = rng.poisson(trips_per_week * weeks * drivers["_activity"].to_numpy())
    idx = np.repeat(np.arange(n), n_trips)
    total = idx.size

    r = drivers["latent_risk"].to_numpy()[idx]
    speed_q = drivers["_speed_quirk"].to_numpy()[idx]
    brake_q = drivers["_brake_quirk"].to_numpy()[idx]
    accel_q = drivers["_accel_quirk"].to_numpy()[idx]
    corner_q = drivers["_corner_quirk"].to_numpy()[idx]
    phone_q = drivers["_phone_quirk"].to_numpy()[idx]
    night_q = drivers["_night_quirk"].to_numpy()[idx]
    region_mult = drivers["region"].map(REGION_DIST).to_numpy()[idx]

    distance = np.clip(rng.lognormal(np.log(14 * region_mult), 0.6), 1, 150)

    is_night = rng.random(total) < sigmoid(-2.0 + 0.35 * r + night_q)
    start_hour = np.where(
        is_night,
        rng.choice(NIGHT_HOURS, size=total),
        rng.choice(DAY_HOURS, size=total, p=DAY_WEIGHTS),
    )

    mean_speeding = sigmoid(-2.6 + 0.45 * r + speed_q)
    k = 8.0  # beta concentration
    pct_speeding = 100 * rng.beta(mean_speeding * k, (1 - mean_speeding) * k)

    per100 = distance / 100
    harsh_brakes = rng.poisson(6 * np.exp(0.30 * r + brake_q) * per100)
    harsh_accels = rng.poisson(4 * np.exp(0.25 * r + accel_q) * per100)
    harsh_corners = rng.poisson(3 * np.exp(0.20 * r + corner_q) * per100)
    phone_use_sec = rng.poisson(300 * np.exp(0.40 * r + phone_q) * per100)

    trip_date = start_date + pd.to_timedelta(rng.integers(0, weeks * 7, total), unit="D")

    trips = pd.DataFrame(
        {
            "trip_id": [f"T{i + 1:07d}" for i in range(total)],
            "driver_id": drivers["driver_id"].to_numpy()[idx],
            "trip_date": trip_date.date if hasattr(trip_date, "date") else trip_date,
            "start_hour": start_hour,
            "distance_km": distance.round(2),
            "pct_time_speeding": pct_speeding.round(2),
            "harsh_brakes": harsh_brakes,
            "harsh_accels": harsh_accels,
            "harsh_corners": harsh_corners,
            "phone_use_sec": phone_use_sec,
        }
    )
    return trips.sort_values(["trip_date", "trip_id"]).reset_index(drop=True)


def make_claims(rng, drivers, trips, weeks, start_date):
    km = trips.groupby("driver_id")["distance_km"].sum()
    km = km.reindex(drivers["driver_id"]).fillna(0).to_numpy()
    exposure = km / km.mean()  # 1.0 = average distance driven

    lam = (
        BASE_ANNUAL_CLAIM_FREQ
        * (weeks / 52)
        * exposure
        * np.exp(RISK_CLAIM_EFFECT * drivers["latent_risk"].to_numpy())
    )
    counts = rng.poisson(lam)
    idx = np.repeat(np.arange(len(drivers)), counts)
    risk = drivers["latent_risk"].to_numpy()[idx]

    severity = rng.gamma(shape=2.0, scale=12500 * np.exp(0.1 * risk))
    claim_date = start_date + pd.to_timedelta(rng.integers(0, weeks * 7, idx.size), unit="D")

    return pd.DataFrame(
        {
            "claim_id": [f"C{i + 1:05d}" for i in range(idx.size)],
            "driver_id": drivers["driver_id"].to_numpy()[idx],
            "claim_date": claim_date.date if hasattr(claim_date, "date") else claim_date,
            "severity_zar": severity.round(2),
        }
    )


def sanity_report(drivers, trips, claims, weeks):
    print("\n=== Sanity checks ===")
    print(f"Drivers: {len(drivers):,} | Trips: {len(trips):,} | Claims: {len(claims):,}")

    t = trips.copy()
    t["night"] = ((t["start_hour"] >= 21) | (t["start_hour"] < 5)).astype(int)
    t["speed_km"] = t["pct_time_speeding"] * t["distance_km"]
    per = t.groupby("driver_id").agg(
        km=("distance_km", "sum"),
        speed_km=("speed_km", "sum"),
        brakes=("harsh_brakes", "sum"),
        accels=("harsh_accels", "sum"),
        corners=("harsh_corners", "sum"),
        phone=("phone_use_sec", "sum"),
        night=("night", "mean"),
    )
    per["pct_speeding"] = per["speed_km"] / per["km"]
    for c in ["brakes", "accels", "corners", "phone"]:
        per[c + "_per100km"] = per[c] / per["km"] * 100
    per = per.join(drivers.set_index("driver_id")["latent_risk"])

    feats = ["pct_speeding", "brakes_per100km", "accels_per100km",
             "corners_per100km", "phone_per100km", "night"]
    print("\nCorrelation of driver-level behaviour with hidden latent_risk")
    print("(should be clearly positive but well below 1):")
    print(per[feats + ["latent_risk"]].corr()["latent_risk"].drop("latent_risk").round(2).to_string())

    n_claims = claims.groupby("driver_id").size().reindex(drivers["driver_id"]).fillna(0)
    d = drivers.assign(claims=n_claims.to_numpy())
    d["risk_quartile"] = pd.qcut(d["latent_risk"], 4, labels=["Q1 low", "Q2", "Q3", "Q4 high"])
    summary = d.groupby("risk_quartile", observed=True)["claims"].mean() * 52 / weeks
    print("\nAnnualised claims per driver by hidden-risk quartile:")
    print(summary.round(3).to_string())
    print(f"\nTotal km per driver: mean {per['km'].mean():,.0f}, "
          f"min {per['km'].min():,.0f}, max {per['km'].max():,.0f}")
    print(f"Mean claim severity: R{claims['severity_zar'].mean():,.0f}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--drivers", type=int, default=5000)
    p.add_argument("--weeks", type=int, default=26)
    p.add_argument("--trips-per-week", type=float, default=6.0)
    p.add_argument("--start-date", default="2026-01-05")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="data")
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    start = pd.Timestamp(args.start_date)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    drivers = make_drivers(rng, args.drivers)
    trips = make_trips(rng, drivers, args.weeks, start, args.trips_per_week)
    claims = make_claims(rng, drivers, trips, args.weeks, start)

    public_cols = ["driver_id", "age_band", "region", "vehicle_age",
                   "latent_risk", "engagement_propensity"]
    drivers[public_cols].to_csv(out / "drivers.csv", index=False)
    trips.to_csv(out / "trips.csv", index=False)
    claims.to_csv(out / "claims.csv", index=False)
    print(f"Wrote drivers.csv, trips.csv, claims.csv to {out.resolve()}")

    sanity_report(drivers, trips, claims, args.weeks)


if __name__ == "__main__":
    main()
