"""
Step 4: simulate three reward designs and compare them with a no-rewards control.

Designs
  A  Flat cashback        R10 per week when the weekly score is >= 80
  B  Streaks              the same goal, but the reward grows with consecutive
                          weeks met (up to 2x) and a miss resets the streak
  C  Tiers + leaderboard  weekly reward by tier (bronze/silver/gold), plus a
                          monthly bonus for the top 10% of drivers
  Control                 no rewards (drivers still have some organic effort)

EVERYTHING BELOW THE "ASSUMPTIONS" HEADING IS AN ASSUMPTION, NOT AN EMPIRICAL
FINDING. The simulation cannot show which design works in reality. It shows
how the conclusions depend on the assumptions, and the sensitivity checks at
the end show how fragile they are.

Mechanics (each week, each driver)
  1. A design-specific "pull" (a log-odds shift) changes the probability that
     the driver makes an effort to drive better this week.
  2. Effort raises the driver's underlying score by a random gain that is
     larger when there is more room to improve. Gains decay by 10% a week.
  3. The observed weekly score is the underlying score plus noise, so goals
     are hit or missed partly by luck.
  4. Rewards are paid from the observed score.

Economics
  Estimated claims saving = expected claims cost at the control group's
  average underlying score minus expected claims cost at the design's.
  Expected claims cost uses the score-to-claims relationship estimated in
  step 3 (about 0.80x claim rate per +10 points). This treats a between-driver
  relationship as if it held within a driver over time, which is a causal
  assumption, so savings are only as good as that assumption.

Random draws are shared across designs (common random numbers), so differences
between designs come from the design, not from luck.

Usage:
    python src/simulate_rewards.py --data data --report-dir report
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import rankdata

from evaluate import load, poisson_irls

# ============================ ASSUMPTIONS ====================================
GOAL = 80.0                  # weekly score goal for designs A and B
R_BASE = 10.0                # base weekly reward in ZAR
NOISE_SD = 6.0               # week-to-week noise in the observed score (points)
DECAY = 0.10                 # share of accumulated improvement lost each week
GAIN_LOW, GAIN_HIGH = 0.5, 4.5   # points gained in a week of effort (before room factor)
ORGANIC_SHARE = 0.5          # control: weekly effort probability = 0.5 x engagement_propensity

PULL_GOAL = 1.2              # log-odds boost when a flat goal is within reach
GOAL_SCALE = 15.0            # points of gap at which goal pull falls to 1/e

STREAK_PULL_STEP = 0.25      # B: pull grows by 25% per streak week...
STREAK_PULL_CAP = 8          # ...up to 8 weeks
STREAK_REWARD_STEP = 0.25    # B: reward multiplier grows 0.25 per extra week...
STREAK_REWARD_CAP = 2.0      # ...capped at 2x
SHOCK = 0.6                  # B: log-odds drop after losing a long streak ("what-the-hell" effect)
SHOCK_MIN_STREAK = 4
SHOCK_WEEKS = 2

TIER_THRESHOLDS = np.array([65.0, 75.0, 85.0])   # bronze, silver, gold
TIER_REWARDS = np.array([0.0, 5.0, 10.0, 15.0])  # none, bronze, silver, gold (ZAR / week)
TIER_PULL = 0.9              # C: pull from being near the next tier
TIER_SCALE = 10.0
RANK_PULL = 0.8              # C: pull scaled by leaderboard percentile (leaders defend position)
BONUS_EVERY = 4              # C: monthly bonus (every 4 weeks)
BONUS_TOP_SHARE = 0.10
BONUS_ZAR = 50.0
# =============================================================================

DESIGNS = ["A_flat_cashback", "B_streaks", "C_tiers_leaderboard"]
LABELS = {"Control": "Control (no rewards)", "A_flat_cashback": "A: Flat cashback",
          "B_streaks": "B: Streaks", "C_tiers_leaderboard": "C: Tiers + leaderboard"}


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def draw_noise(rng, weeks, n):
    return {
        "U": rng.random((weeks, n)),
        "G": rng.uniform(GAIN_LOW, GAIN_HIGH, (weeks, n)),
        "E": rng.normal(0, NOISE_SD, (weeks, n)),
    }


def simulate(design, base, prop, noise, weeks, resp=1.0, shock=SHOCK):
    n = base.size
    p0 = np.clip(ORGANIC_SHARE * prop, 0.01, 0.99)
    logit0 = np.log(p0 / (1 - p0))

    improve = np.zeros(n)
    s_prev = base.copy()
    streak = np.zeros(n, dtype=int)
    shock_left = np.zeros(n, dtype=int)
    hist = [base.copy() for _ in range(4)]  # last 4 observed scores

    rec = {k: np.zeros((weeks, n)) for k in ["tried", "S", "u", "reward", "streak", "tier"]}

    for t in range(weeks):
        # 1. pull
        if design == "Control":
            pull = np.zeros(n)
        elif design in ("A_flat_cashback", "B_streaks"):
            g = np.exp(-np.maximum(0, GOAL - s_prev) / GOAL_SCALE)
            pull = PULL_GOAL * g
            if design == "B_streaks":
                pull = pull * (1 + STREAK_PULL_STEP * np.minimum(streak, STREAK_PULL_CAP))
                pull = pull - shock * (shock_left > 0)
        else:  # C
            idx = np.searchsorted(TIER_THRESHOLDS, s_prev, side="right")
            gap = np.maximum(0, TIER_THRESHOLDS[np.minimum(idx, 2)] - s_prev)
            pct_prev = rankdata(np.mean(hist, axis=0)) / n
            pull = TIER_PULL * np.exp(-gap / TIER_SCALE) + RANK_PULL * pct_prev

        # 2. effort -> improvement
        tried = noise["U"][t] < sigmoid(logit0 + resp * pull)
        u_prev = np.clip(base + improve, 0, 100)
        room = np.clip((95 - u_prev) / 40, 0.1, 1.0)
        improve = (1 - DECAY) * improve + tried * noise["G"][t] * room

        # 3. observed score
        u = np.clip(base + improve, 0, 100)
        S = np.clip(u + noise["E"][t], 0, 100)
        hist = hist[1:] + [S]

        # 4. rewards and state updates
        goal = S >= GOAL
        reward = np.zeros(n)
        tier = np.zeros(n, dtype=int)
        if design in ("A_flat_cashback", "B_streaks"):
            new_streak = np.where(goal, streak + 1, 0)
            if design == "A_flat_cashback":
                reward = R_BASE * goal
            else:
                mult = np.minimum(1 + STREAK_REWARD_STEP * (new_streak - 1), STREAK_REWARD_CAP)
                reward = R_BASE * mult * goal
                lost = (~goal) & (streak >= SHOCK_MIN_STREAK)
                shock_left = np.where(lost, SHOCK_WEEKS, np.maximum(shock_left - 1, 0))
            streak = new_streak
        elif design == "C_tiers_leaderboard":
            tier = np.searchsorted(TIER_THRESHOLDS, S, side="right")
            reward = TIER_REWARDS[tier].copy()
            if (t + 1) % BONUS_EVERY == 0:
                m4 = np.mean(hist, axis=0)
                reward = reward + BONUS_ZAR * (m4 >= np.quantile(m4, 1 - BONUS_TOP_SHARE))

        s_prev = S
        rec["tried"][t], rec["S"][t], rec["u"][t] = tried, S, u
        rec["reward"][t], rec["streak"][t], rec["tier"][t] = reward, streak, tier
    return rec


def economics(sim, ctrl, exposure, b0, b1, severity):
    cost = lambda s: np.exp(b0 + b1 * s / 10) * exposure * severity
    s_des, s_ctl = sim["u"].mean(axis=0), ctrl["u"].mean(axis=0)
    saving = cost(s_ctl) - cost(s_des)
    reward = sim["reward"].sum(axis=0)
    return saving, reward, s_des - s_ctl


def run_scenario(base, prop, exposure, econ, weeks, seed, resp=1.0, shock=SHOCK, gain_mult=1.0):
    rng = np.random.default_rng(seed)
    noise = draw_noise(rng, weeks, base.size)
    noise["G"] = noise["G"] * gain_mult  # how much a week of effort raises the score
    ctrl = simulate("Control", base, prop, noise, weeks)
    out = {}
    for d in DESIGNS:
        sim = simulate(d, base, prop, noise, weeks, resp=resp, shock=shock)
        saving, reward, uplift = economics(sim, ctrl, exposure, *econ)
        out[d] = {"sim": sim, "saving": saving, "reward": reward, "uplift": uplift}
    return ctrl, out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data")
    p.add_argument("--weeks", type=int, default=26)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--mc-seeds", type=int, default=10)
    p.add_argument("--report-dir", default="report")
    args = p.parse_args()
    data, report = Path(args.data), Path(args.report_dir)
    report.mkdir(parents=True, exist_ok=True)
    W = args.weeks
    pd.set_option("display.width", 170)

    # ---- inputs ---------------------------------------------------------------
    df = load(data, W)
    drv = pd.read_csv(data / "drivers.csv")[["driver_id", "engagement_propensity"]]
    df = df.merge(drv, on="driver_id")
    claims = pd.read_csv(data / "claims.csv")
    severity = claims["severity_zar"].mean()
    base = df["score"].to_numpy()
    prop = df["engagement_propensity"].to_numpy()
    exposure = df["exposure"].to_numpy()
    n = base.size

    # score -> claims relationship (same model as step 3, "score alone")
    beta, _ = poisson_irls((base / 10).reshape(-1, 1), df["claims"].to_numpy(), np.log(exposure))
    econ = (beta[0], beta[1], severity)
    print(f"Drivers: {n:,} | weeks: {W} | mean claim severity: R{severity:,.0f}")
    print(f"Claim-rate multiplier per +10 score points used for savings: {np.exp(beta[1]):.2f}")

    # ---- base run ---------------------------------------------------------------
    ctrl, res = run_scenario(base, prop, exposure, econ, W, args.seed)
    terc = pd.qcut(base, 3, labels=["Lowest third", "Middle third", "Highest third"])

    rows = []
    for d in DESIGNS:
        r = res[d]
        sim = r["sim"]
        cost, sav = r["reward"], r["saving"]
        row = {
            "design": LABELS[d],
            "effort_rate": sim["tried"].mean(),
            "effort_rate_control": ctrl["tried"].mean(),
            "goal_hit_rate": (sim["S"] >= GOAL).mean(),
            "share_driver_weeks_rewarded": (sim["reward"] > 0).mean(),
            "avg_score_uplift_pts": r["uplift"].mean(),
            "reward_cost_per_driver": cost.mean(),
            "claims_saving_per_driver": sav.mean(),
            "net_per_driver": sav.mean() - cost.mean(),
            "saving_per_R1_reward": sav.sum() / cost.sum(),
            # uplift needed for saving to equal cost, assuming saving scales with uplift
            "breakeven_uplift_pts": cost.mean() / (sav.mean() / r["uplift"].mean()),
        }
        for lab in terc.categories:
            row[f"uplift_{lab.split()[0].lower()}_third"] = r["uplift"][terc == lab].mean()
        rows.append(row)
    summ = pd.DataFrame(rows).set_index("design")
    print(f"\n=== Base scenario (seed {args.seed}; control effort rate {ctrl['tried'].mean():.2f}) ===")
    print(summ.drop(columns=["effort_rate_control"]).round(2).T.to_string())
    summ.to_csv(report / "reward_design_summary.csv")

    # ---- Monte Carlo across seeds (noise in the simulation itself) -------------
    mc = {d: [] for d in DESIGNS}
    for s in range(1, args.mc_seeds + 1):
        _, rr = run_scenario(base, prop, exposure, econ, W, 1000 + s)
        for d in DESIGNS:
            mc[d].append(rr[d]["saving"].mean() - rr[d]["reward"].mean())
    mc_arr = np.array([mc[d] for d in DESIGNS])  # designs x seeds
    best = mc_arr.argmax(axis=0)
    print(f"\n=== Simulation noise: net benefit per driver across {args.mc_seeds} random seeds ===")
    for i, d in enumerate(DESIGNS):
        print(f"  {LABELS[d]:24s} mean R{mc_arr[i].mean():7.1f}  sd R{mc_arr[i].std(ddof=1):5.1f}  "
              f"best in {np.mean(best == i) * 100:3.0f}% of seeds")

    # ---- Sensitivity ------------------------------------------------------------
    print("\n=== Sensitivity: net benefit per driver (ZAR, saving minus reward cost) ===")
    print("resp      = how strongly drivers react to incentives (1.0 = base)")
    print("gain_mult = how much a week of effort raises the score (1.0 = base)")
    sens_rows = []
    for resp in (0.5, 1.0, 1.5):
        for gm in (1.0, 2.0, 3.0):
            _, rr = run_scenario(base, prop, exposure, econ, W, args.seed, resp=resp, gain_mult=gm)
            rec = {"responsiveness": resp, "gain_multiplier": gm}
            for d in DESIGNS:
                rec[d] = rr[d]["saving"].mean() - rr[d]["reward"].mean()
                rec[d + "_uplift"] = rr[d]["uplift"].mean()
            rec["best_net"] = LABELS[max(DESIGNS, key=lambda d: rec[d])]
            sens_rows.append(rec)
    sens = pd.DataFrame(sens_rows)
    show = sens[["responsiveness", "gain_multiplier"] + DESIGNS + ["best_net"]].copy()
    show.columns = ["resp", "gain_mult", "A net", "B net", "C net", "best (by net)"]
    print(show.round(1).to_string(index=False))
    sens.to_csv(report / "reward_sensitivity.csv", index=False)
    flips = sens["best_net"].nunique() > 1
    any_pos = {LABELS[d]: int((sens[d] > 0).sum()) for d in DESIGNS}
    print("\nBest design by net benefit " + ("CHANGES across scenarios." if flips else "is the same in every scenario."))
    print(f"Scenarios (of {len(sens)}) where net benefit is positive: " +
          ", ".join(f"{k.split(':')[0]}={v}" for k, v in any_pos.items()))

    shock_net = {}
    for sh in (0.0, SHOCK, 1.2):
        _, rr = run_scenario(base, prop, exposure, econ, W, args.seed, shock=sh)
        shock_net[sh] = rr["B_streaks"]["saving"].mean() - rr["B_streaks"]["reward"].mean()
    print("Streak-loss shock in B (0 / base / 1.2): net per driver R"
          + " / R".join(f"{v:.1f}" for v in shock_net.values())
          + "  -> B is insensitive to it here (long streaks are rare when ~1 in 3 weeks meets the goal).")

    # ---- Outputs for SQL / Power BI --------------------------------------------
    ids = df["driver_id"].to_numpy()
    frames, weekly = [], []
    all_sims = {"Control": (ctrl, np.zeros(n), np.zeros(n))}
    for d in DESIGNS:
        all_sims[d] = (res[d]["sim"], res[d]["saving"], res[d]["reward"])
    for d, (sim, _, _) in all_sims.items():
        tier_names = np.array(["none", "bronze", "silver", "gold"])[sim["tier"].astype(int)]
        frames.append(pd.DataFrame({
            "driver_id": np.tile(ids, W),
            "week": np.repeat(np.arange(1, W + 1), n),
            "design": LABELS[d],
            "weekly_score": sim["S"].ravel().round(2),
            "goal_met": (sim["S"] >= GOAL).ravel().astype(int),
            "streak_length": sim["streak"].ravel().astype(int),
            "tier": (tier_names.ravel() if d == "C_tiers_leaderboard" else ""),
            "engaged": sim["tried"].ravel().astype(int),
            "reward_zar": sim["reward"].ravel().round(2),
        }))
        weekly.append(pd.DataFrame({
            "design": LABELS[d], "week": np.arange(1, W + 1),
            "effort_rate": sim["tried"].mean(axis=1),
            "goal_hit_rate": (sim["S"] >= GOAL).mean(axis=1),
            "mean_observed_score": sim["S"].mean(axis=1),
            "share_rewarded": (sim["reward"] > 0).mean(axis=1),
            "reward_cost_total": sim["reward"].sum(axis=1),
        }))
    pd.concat(frames).to_csv(data / "reward_events.csv", index=False)
    pd.concat(weekly).round(4).to_csv(report / "weekly_design_metrics.csv", index=False)

    outcome_frames = []
    for d, (sim, saving, reward) in all_sims.items():
        outcome_frames.append(pd.DataFrame({
            "driver_id": ids, "design": LABELS[d], "baseline_score": base.round(2),
            "baseline_third": terc.astype(str),
            "avg_underlying_score": sim["u"].mean(axis=0).round(2),
            "reward_cost_zar": reward.round(2), "claims_saving_zar": saving.round(2),
        }))
    pd.concat(outcome_frames).to_csv(data / "driver_design_outcomes.csv", index=False)

    # ---- Chart ---------------------------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    ax = axes[0]
    xs = np.arange(len(DESIGNS))
    ax.bar(xs - 0.2, summ["claims_saving_per_driver"], 0.4, label="Est. claims saving", color="#54A24B")
    ax.bar(xs + 0.2, summ["reward_cost_per_driver"], 0.4, label="Reward cost", color="#E45756")
    ax.set_xticks(xs)
    ax.set_xticklabels(["A: Flat", "B: Streaks", "C: Tiers +\nleaderboard"])
    ax.set_ylabel("ZAR per driver over 26 weeks")
    ax.set_title("Estimated saving vs reward cost (assumption-driven)")
    ax.legend()
    ax = axes[1]
    wk = pd.concat(weekly)
    for d, colour in zip(["Control"] + DESIGNS, ["#999999", "#4C78A8", "#F58518", "#B279A2"]):
        w = wk[wk["design"] == LABELS[d]]
        ax.plot(w["week"], w["goal_hit_rate"] * 100, label=LABELS[d], color=colour)
    ax.set_xlabel("Week")
    ax.set_ylabel(f"Drivers with weekly score >= {int(GOAL)} (%)")
    ax.set_ylim(0, 40)  # start at zero so small differences are not exaggerated
    ax.set_title("Goal hit rate over time")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(report / "reward_designs.png", dpi=150)
    plt.close(fig)

    summary = {
        "base": summ.round(3).to_dict(orient="index"),
        "monte_carlo_net_per_driver": {
            LABELS[d]: {"mean": round(float(mc_arr[i].mean()), 1), "sd": round(float(mc_arr[i].std(ddof=1)), 1),
                        "best_share": round(float(np.mean(best == i)), 2)}
            for i, d in enumerate(DESIGNS)},
        "ranking_changes_in_sensitivity": bool(flips),
        "sensitivity_positive_net_counts": any_pos,
        "streak_shock_net_B": {str(k): round(float(v), 1) for k, v in shock_net.items()},
    }
    (report / "reward_simulation_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved reward_events.csv and driver_design_outcomes.csv to {data.resolve()}")
    print(f"Saved summaries, sensitivity, weekly metrics and chart to {report.resolve()}")


if __name__ == "__main__":
    main()
