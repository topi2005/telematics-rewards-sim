"""
Step 3: validate the driver score against claims.

Questions answered:
  1. Does claim frequency fall as the score rises? (decile table with 95% CIs)
  2. How well does the score rank drivers by risk? (lift and Gini, bootstrapped)
  3. How do the hand-picked weights compare with a Poisson regression
     fitted on claims, and does that regression rank drivers better
     out of sample?
  4. Does the score carry information beyond age band and region?

Why this is not circular: the score (src/score.py) is built from trip
behaviour only. Claims were generated from the hidden `latent_risk`, never
from the score. `latent_risk` is used here ONLY to compute an "oracle"
ceiling (the best ranking any behaviour-based score could aim for). That
number exists only because the data is synthetic.

Exposure is relative distance driven: 1.0 = the average driver over a full
year, so frequencies read as "claims per average driver-year".

Usage:
    python src/evaluate.py --data data --weeks 26 --report-dir report
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import chi2, spearmanr

from score import COMPONENTS


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
def load(data_dir: Path, weeks: int) -> pd.DataFrame:
    drivers = pd.read_csv(data_dir / "drivers.csv")
    scored = pd.read_csv(data_dir / "drivers_scored.csv")
    claims = pd.read_csv(data_dir / "claims.csv")

    df = scored.merge(
        drivers[["driver_id", "age_band", "region", "latent_risk"]], on="driver_id"
    )
    df["claims"] = df["driver_id"].map(claims.groupby("driver_id").size()).fillna(0).astype(int)
    df["exposure"] = df["km"] / df["km"].mean() * (weeks / 52)
    return df


# ----------------------------------------------------------------------------
# Poisson regression with exposure offset (IRLS), with standard errors
# ----------------------------------------------------------------------------
def poisson_irls(X, y, offset, n_iter=100, tol=1e-9):
    X = np.column_stack([np.ones(len(X)), X])
    beta = np.zeros(X.shape[1])
    beta[0] = np.log(y.sum() / np.exp(offset).sum())
    for _ in range(n_iter):
        eta = X @ beta + offset
        mu = np.exp(eta)
        z = eta - offset + (y - mu) / mu
        XtW = X.T * mu
        new = np.linalg.solve(XtW @ X, XtW @ z)
        if np.max(np.abs(new - beta)) < tol:
            beta = new
            break
        beta = new
    mu = np.exp(X @ beta + offset)
    cov = np.linalg.inv((X.T * mu) @ X)
    return beta, np.sqrt(np.diag(cov))


def predict_rate(beta, X):
    """Predicted claims per unit exposure."""
    return np.exp(np.column_stack([np.ones(len(X)), X]) @ beta)


# ----------------------------------------------------------------------------
# Ranking metrics
# ----------------------------------------------------------------------------
def gini(risk, claims, exposure):
    """Insurance Gini: 0 = no better than random, 1 = perfect ranking.

    Drivers are ordered safest-first by predicted risk; the Lorenz curve
    plots cumulative share of exposure against cumulative share of claims.
    """
    order = np.argsort(risk, kind="stable")
    c = np.concatenate([[0], np.cumsum(claims[order]) / claims.sum()])
    e = np.concatenate([[0], np.cumsum(exposure[order]) / exposure.sum()])
    return 1 - 2 * np.trapezoid(c, e)


def lorenz(risk, claims, exposure):
    order = np.argsort(risk, kind="stable")
    c = np.concatenate([[0], np.cumsum(claims[order]) / claims.sum()])
    e = np.concatenate([[0], np.cumsum(exposure[order]) / exposure.sum()])
    return e, c


def bootstrap_ginis(claims, exposure, risks, n=500, seed=1):
    rng = np.random.default_rng(seed)
    out = {k: [] for k in risks}
    m = len(claims)
    for _ in range(n):
        idx = rng.integers(0, m, m)
        if claims[idx].sum() == 0:
            continue
        for k, r in risks.items():
            out[k].append(gini(r[idx], claims[idx], exposure[idx]))
    return {k: np.array(v) for k, v in out.items()}


def ci(a):
    return np.percentile(a, [2.5, 97.5])


# ----------------------------------------------------------------------------
# Score bands
# ----------------------------------------------------------------------------
def band_table(df, bins):
    d = df.copy()
    d["band"] = pd.qcut(d["score"].rank(method="first"), bins, labels=range(1, bins + 1))
    g = d.groupby("band", observed=True).agg(
        drivers=("driver_id", "count"),
        min_score=("score", "min"),
        max_score=("score", "max"),
        claims=("claims", "sum"),
        exposure=("exposure", "sum"),
    )
    g["frequency"] = g["claims"] / g["exposure"]
    lo = np.where(g["claims"] > 0, chi2.ppf(0.025, 2 * g["claims"].clip(lower=1)) / 2, 0)
    hi = chi2.ppf(0.975, 2 * (g["claims"] + 1)) / 2
    g["freq_lo"] = lo / g["exposure"]
    g["freq_hi"] = hi / g["exposure"]
    g["relative_to_avg"] = g["frequency"] / (df["claims"].sum() / df["exposure"].sum())
    return g


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data")
    p.add_argument("--weeks", type=int, default=26)
    p.add_argument("--report-dir", default="report")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--boot", type=int, default=500)
    args = p.parse_args()

    report = Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    df = load(Path(args.data), args.weeks)
    summary = {}

    pd.set_option("display.width", 160)
    print(f"Drivers: {len(df):,} | Claims: {df['claims'].sum():,} | "
          f"Portfolio frequency: {df['claims'].sum() / df['exposure'].sum():.3f} claims per average driver-year")

    # ---- 1. Decile table -----------------------------------------------------
    dec = band_table(df, 10)
    print("\n=== 1. Claim frequency by score decile (1 = lowest scores, riskiest) ===")
    print(dec.round({"min_score": 1, "max_score": 1, "exposure": 1, "frequency": 3,
                     "freq_lo": 3, "freq_hi": 3, "relative_to_avg": 2}).to_string())
    rho, _ = spearmanr(dec.index.astype(int), dec["frequency"])
    print(f"Spearman correlation (decile vs frequency): {rho:.2f}")
    dec.to_csv(report / "decile_table.csv")

    quint = band_table(df, 5)
    q_ratio = quint["frequency"].iloc[0] / quint["frequency"].iloc[-1]
    print(f"\nLowest-score quintile vs highest-score quintile frequency ratio: {q_ratio:.1f}x "
          f"({int(quint['claims'].iloc[0])} vs {int(quint['claims'].iloc[-1])} claims)")
    summary["decile_spearman"] = round(float(rho), 3)
    summary["quintile_ratio_lowest_vs_highest"] = round(float(q_ratio), 2)
    summary["quintile_claims_lowest"] = int(quint["claims"].iloc[0])
    summary["quintile_claims_highest"] = int(quint["claims"].iloc[-1])

    # ---- 2/3. Train-test split, Poisson regression, Gini --------------------
    feat_cols = [f"penalty_{n}" for n in COMPONENTS]
    weights = np.array([w for (w, *_rest) in COMPONENTS.values()], dtype=float)
    X_all = df[feat_cols].to_numpy() / weights  # fraction of the way from floor to cap
    y = df["claims"].to_numpy()
    off = np.log(df["exposure"].to_numpy())

    rng = np.random.default_rng(args.seed)
    is_test = rng.random(len(df)) < 0.30
    tr, te = ~is_test, is_test

    beta_tr, _ = poisson_irls(X_all[tr], y[tr], off[tr])
    test = df[te].reset_index(drop=True)
    risks_test = {
        "Hand-built score": -test["score"].to_numpy(),
        "Poisson regression": predict_rate(beta_tr, X_all[te]),
        "Oracle (hidden risk)": test["latent_risk"].to_numpy(),
    }
    ct, et = test["claims"].to_numpy(), test["exposure"].to_numpy()
    boots = bootstrap_ginis(ct, et, risks_test, n=args.boot)

    print(f"\n=== 2. Ranking power on held-out drivers "
          f"({te.sum():,} drivers, {int(ct.sum())} claims) ===")
    print("Gini: 0 = random ranking, 1 = perfect. 95% CI from bootstrap over drivers.")
    gin = {}
    for k, r in risks_test.items():
        g = gini(r, ct, et)
        lo, hi = ci(boots[k])
        gin[k] = (g, lo, hi)
        print(f"  {k:22s} {g:5.2f}   [{lo:.2f}, {hi:.2f}]")
    diff = boots["Hand-built score"] - boots["Poisson regression"]
    dlo, dhi = ci(diff)
    print(f"  Hand-built minus Poisson Gini: {diff.mean():+.3f}  [{dlo:+.3f}, {dhi:+.3f}]")
    summary["gini_test"] = {k: {"gini": round(float(v[0]), 3), "ci_lo": round(float(v[1]), 3),
                                "ci_hi": round(float(v[2]), 3)} for k, v in gin.items()}
    summary["gini_hand_minus_poisson"] = {"mean": round(float(diff.mean()), 3),
                                          "ci_lo": round(float(dlo), 3), "ci_hi": round(float(dhi), 3)}
    summary["test_drivers"] = int(te.sum())
    summary["test_claims"] = int(ct.sum())

    # Full-sample Gini for the hand-built score: it is not fitted to claims, so
    # no split is needed and all claims can be used for a tighter estimate.
    ca, ea = df["claims"].to_numpy(), df["exposure"].to_numpy()
    risks_all = {"Hand-built score": -df["score"].to_numpy(),
                 "Oracle (hidden risk)": df["latent_risk"].to_numpy()}
    boots_all = bootstrap_ginis(ca, ea, risks_all, n=args.boot)
    print(f"\nFull sample ({len(df):,} drivers, {int(ca.sum())} claims; the hand-built score is not fitted, "
          f"so no split is needed):")
    summary["gini_full_sample"] = {}
    for k, r in risks_all.items():
        g = gini(r, ca, ea)
        lo, hi = ci(boots_all[k])
        print(f"  {k:22s} {g:5.2f}   [{lo:.2f}, {hi:.2f}]")
        summary["gini_full_sample"][k] = {"gini": round(float(g), 3), "ci_lo": round(float(lo), 3),
                                          "ci_hi": round(float(hi), 3)}

    # ---- 3. Weights vs Poisson coefficients (all drivers, descriptive) -------
    beta, se = poisson_irls(X_all, y, off)
    coef, cse = beta[1:], se[1:]
    pos = np.clip(coef, 0, None)
    implied = pos / pos.sum() * 100 if pos.sum() > 0 else np.zeros_like(pos)
    comp = pd.DataFrame(
        {
            "hand_weight": weights,
            "poisson_coef": coef,
            "std_error": cse,
            "rate_ratio_floor_to_cap": np.exp(coef),
            "rr_lo": np.exp(coef - 1.96 * cse),
            "rr_hi": np.exp(coef + 1.96 * cse),
            "implied_weight": implied,
        },
        index=list(COMPONENTS),
    )
    print("\n=== 3. Hand-picked weights vs Poisson regression (all drivers) ===")
    print("rate_ratio = claim-rate multiplier for moving from floor to cap on that behaviour alone,")
    print("holding the others fixed. implied_weight = positive coefficients rescaled to sum to 100.")
    print(comp.round(2).to_string())
    comp.to_csv(report / "weight_comparison.csv")
    summary["weight_comparison"] = {
        k: {"hand": float(r.hand_weight), "implied": round(float(r.implied_weight), 1),
            "rate_ratio": round(float(r.rate_ratio_floor_to_cap), 2),
            "rr_lo": round(float(r.rr_lo), 2), "rr_hi": round(float(r.rr_hi), 2)}
        for k, r in comp.iterrows()
    }

    # ---- 4. Is the score just a proxy for age band / region? ----------------
    print("\n=== 4. Proxy check: age band and region ===")
    print("Mean score by group:")
    for col in ["age_band", "region"]:
        print(df.groupby(col)["score"].agg(["count", "mean"]).round(1).to_string(), "\n")

    s10 = (df["score"] / 10).to_numpy().reshape(-1, 1)
    dummies = pd.get_dummies(df[["age_band", "region"]], drop_first=True).astype(float)
    b1, s1 = poisson_irls(s10, y, off)
    b2, s2 = poisson_irls(np.column_stack([s10, dummies.to_numpy()]), y, off)
    rr1 = (np.exp(b1[1]), np.exp(b1[1] - 1.96 * s1[1]), np.exp(b1[1] + 1.96 * s1[1]))
    rr2 = (np.exp(b2[1]), np.exp(b2[1] - 1.96 * s2[1]), np.exp(b2[1] + 1.96 * s2[1]))
    print("Claim-rate multiplier per +10 score points:")
    print(f"  score alone:                    {rr1[0]:.2f}  [{rr1[1]:.2f}, {rr1[2]:.2f}]")
    print(f"  score + age band + region:      {rr2[0]:.2f}  [{rr2[1]:.2f}, {rr2[2]:.2f}]")
    print("If the second figure stays close to the first, the score is not just\n"
          "standing in for age or region.")
    summary["rate_ratio_per_10_points"] = {
        "score_only": [round(float(v), 3) for v in rr1],
        "score_plus_age_region": [round(float(v), 3) for v in rr2],
    }

    # ---- Charts --------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = dec.index.astype(int)
    ax.bar(x, dec["frequency"], color="#4C78A8")
    ax.errorbar(x, dec["frequency"],
                yerr=[dec["frequency"] - dec["freq_lo"], dec["freq_hi"] - dec["frequency"]],
                fmt="none", ecolor="black", capsize=3, lw=1)
    ax.axhline(df["claims"].sum() / df["exposure"].sum(), color="#E45756", ls="--", label="Portfolio average")
    ax.set_xlabel("Score decile (1 = lowest scores, 10 = highest)")
    ax.set_ylabel("Claims per average driver-year")
    ax.set_title("Claim frequency by driver score decile (synthetic data, 95% CIs)")
    ax.set_xticks(x)
    ax.legend()
    fig.tight_layout()
    fig.savefig(report / "decile_frequency.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    for k, colour in zip(risks_test, ["#4C78A8", "#54A24B", "#B279A2"]):
        e, c = lorenz(risks_test[k], ct, et)
        ax.plot(e, c, label=f"{k} (Gini {gin[k][0]:.2f})", color=colour)
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="Random")
    ax.set_xlabel("Cumulative share of exposure (safest first)")
    ax.set_ylabel("Cumulative share of claims")
    ax.set_title("Lorenz curves on held-out drivers")
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()
    fig.savefig(report / "lorenz_curve.png", dpi=150)
    plt.close(fig)

    (report / "validation_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved decile_table.csv, weight_comparison.csv, validation_summary.json, "
          f"decile_frequency.png, lorenz_curve.png to {report.resolve()}")


if __name__ == "__main__":
    main()
