# Telematics Driver Scoring and Rewards Simulation

A simulation of how an insurer could score driving behaviour from telematics data, check that the score relates to claims risk, and compare reward designs (flat cashback, streaks, tiers and leaderboards) on engagement and economics.

**Built with:** Python, SQL, Excel, Power BI
**Data:** fully synthetic. No real customer or insurer data is used.

## Project status

| Step | Description | Status |
|---|---|---|
| 1 | Synthetic data generation | Done |
| 2 | Explainable driver score | Done |
| 3 | Validation against claims | Done |
| 4 | Rewards simulation | Done |
| 5 | SQL aggregations | Done |
| 6 | Power BI dashboard | To do |
| 7 | One-page summary | To do |


## Purpose

Usage-based motor insurance rewards drivers for driving well. This project explores three questions:

1. Can an explainable behaviour score separate lower-risk drivers from higher-risk ones?
2. Which reward mechanics (flat goals, streaks, tiers) keep drivers engaged at a cost that claims savings could justify?
3. How sensitive are those conclusions to assumptions about how drivers respond?

Because the data is synthetic, the project demonstrates method and reasoning. It does not make claims about how any real insurer or programme performs.

## Key findings

**[fill in after step 6]** 3 to 4 bullets with real numbers, for example:

- Claim frequency in the lowest score decile was X times that of the highest. (Step 3 gives the first numbers: the lowest quintile has 3.6 times the highest quintile's frequency.)
- Reward design B (streaks) achieved the highest engagement but cost Y% more than design A. (Step 4 gives the first numbers: B's weekly effort rate is 0.40 against 0.33 for A, at about 60% higher cost.)
- The ranking of designs changed when driver responsiveness was reduced, so the conclusion depends on that assumption. (Step 4 finds the ranking is stable, but whether any design pays for itself is not.)

## Repository structure

```
telematics-rewards-sim/
├── README.md
├── requirements.txt
├── data/                  # generated CSVs (gitignored; regenerate with the commands below)
├── sql/                   # aggregation queries feeding the dashboard
├── src/
│   ├── generate_data.py   # step 1
│   ├── score.py           # step 2
│   ├── evaluate.py        # step 3
│   ├── simulate_rewards.py  # step 4
│   └── run_sql.py         # step 5: build SQLite db, run queries, export CSVs
├── tests/                 # SQL results reconciled against the Python results
├── notebooks/
├── dashboard/
│   └── data/              # CSV exports from the SQL queries, imported by Power BI
└── report/summary.pdf     # one-page findings
```

## How to run

```bash
pip install -r requirements.txt

# Step 1: generate drivers.csv, trips.csv, claims.csv
python src/generate_data.py --drivers 5000 --weeks 26 --seed 42 --out data

# Step 2: score every driver from trip behaviour
python src/score.py --trips data/trips.csv --out data/drivers_scored.csv

# Step 3: validate the score against claims (writes tables and charts to report/)
python src/evaluate.py --data data --weeks 26 --report-dir report

# Step 4: simulate reward designs (writes reward_events.csv, outcomes and summaries)
python src/simulate_rewards.py --data data --report-dir report

# Step 5: load outputs into SQLite, run sql/*.sql, export CSVs for Power BI
python src/run_sql.py --data data --sql sql --out dashboard/data

# Check the SQL results against the Python results (works with or without pytest)
python tests/test_sql_reconciliation.py
```

The same seed reproduces identical data.

## Data

Three tables are generated (a fourth, `reward_events`, is added in step 4).

| Table | Key fields |
|---|---|
| `drivers` | `driver_id`, `age_band`, `region`, `vehicle_age`, `latent_risk` (hidden), `engagement_propensity` (hidden) |
| `trips` | `trip_id`, `driver_id`, `trip_date`, `start_hour`, `distance_km`, `pct_time_speeding`, `harsh_brakes`, `harsh_accels`, `harsh_corners`, `phone_use_sec` |
| `claims` | `claim_id`, `driver_id`, `claim_date`, `severity_zar` |

**Default size:** 5,000 drivers, about 814,000 trips over 26 weeks, and about 480 claims.

### How the data is generated, and how circularity is avoided

Each driver has a hidden `latent_risk`. Trip behaviour and claims are both generated from it, but **independently of each other**. The score only ever sees trip behaviour. It never reads `latent_risk` or claims. Validation therefore tests whether the score recovers a pattern it was not built from.

Two further design choices keep the score a realistic, imperfect proxy for risk:

- Each driver has behaviour "quirks" unrelated to risk (for example, a driver who brakes harshly out of habit but is otherwise careful). Without these, the score looked unrealistically good: harsh acceleration and cornering initially correlated with hidden risk at 0.91 and 0.84. After adding quirks, driver-level behaviours correlate with hidden risk at 0.31 to 0.62.
- Claim counts follow a Poisson process driven by hidden risk and distance driven. Severity follows a gamma distribution.

All parameters are assumptions. The base annual claim frequency (15%) is set deliberately high so there are enough claims to validate the score. It is not an estimate of real-world frequency.

## Score design

The score runs from 0 to 100, where higher is safer. It is built from five behaviour rates, each converted to a penalty:

```
penalty = weight × clip((rate − floor) / (cap − floor), 0, 1)
score   = 100 − sum of penalties
```

| Component | Weight | Floor | Cap | Unit |
|---|---|---|---|---|
| Speeding | 30 | 3 | 20 | % of trip time speeding (distance-weighted) |
| Harsh braking | 25 | 3 | 16 | harsh brakes per 100 km |
| Harsh acceleration and cornering | 15 | 4 | 15 | events per 100 km |
| Night driving | 15 | 0.03 | 0.30 | share of km driven 21:00 to 05:00 |
| Phone use | 15 | 100 | 900 | seconds per 100 km |

**Why this design**

- It is transparent. Every point lost can be traced to a behaviour, which matters for explaining scores to customers and regulators.
- Rates are per 100 km, so drivers who drive more are not penalised just for exposure.
- Caps sit near the 95th percentile of the synthetic population, and floors near the 10th. Rates below the floor cost nothing.

**Calibration note:** the first version had no floors. The median score was 58 and only 2.7% of drivers reached 80, which made goal-based rewards unworkable. Adding floors gave a median of about 70, with about 26% of drivers at 80 or above.

**Resulting distribution:** mean 66, median 70, 5th percentile 31, 95th percentile 91.

Weights, floors and caps are assumptions. Step 3 compares the weights with a Poisson regression fitted on claims.

## Validation

Run with `python src/evaluate.py`. Claim frequency is measured as claims per average driver-year, where exposure is distance driven relative to the average driver. The portfolio average is 0.19. Hidden risk (`latent_risk`) is used only to compute an "oracle" ceiling for comparison; it never enters the score.

**1. Claim frequency falls as the score rises** (`report/decile_frequency.png`)

- Frequency falls from 0.42 in the lowest-score decile to 0.08 in the highest. The Spearman correlation between decile and frequency is -0.96.
- The lowest-score quintile has 3.6 times the claim frequency of the highest-score quintile (180 vs 52 claims).
- The pattern is not perfectly monotone (decile 8 sits slightly above decile 7), but the 95% intervals overlap, so this is consistent with noise.

**2. Ranking power (Gini)** (`report/lorenz_curve.png`)

| Basis | Hand-built score | Poisson regression | Oracle (hidden risk) |
|---|---|---|---|
| All drivers (481 claims) | 0.25 [0.20, 0.30] | not applicable (fitted on these claims) | 0.30 [0.26, 0.35] |
| Held-out 30% (146 claims) | 0.15 [0.06, 0.25] | 0.13 [0.04, 0.23] | 0.23 [0.14, 0.32] |

- Brackets are 95% bootstrap intervals over drivers.
- Claims are rare, random events, so even a perfect knowledge of hidden risk gives a modest Gini. The fair benchmark is the oracle, and the score reaches roughly five-sixths of it on the full sample.
- The held-out estimate is noisy because it rests on only 146 claims, which is why the full-sample figure is also reported. The hand-built score is not fitted to claims, so using all drivers does not leak information.
- Hand-built minus Poisson Gini on held-out drivers is +0.02 [-0.01, +0.05]. There is no evidence that weights fitted to claims rank drivers better than the hand-picked ones.

**3. Hand-picked weights vs Poisson regression** (`report/weight_comparison.csv`)

| Component | Hand weight | Implied weight | Rate ratio, floor to cap [95% CI] |
|---|---|---|---|
| Speeding | 30 | 41 | 2.57 [1.85, 3.58] |
| Harsh braking | 25 | 7 | 1.17 [0.85, 1.61] |
| Harsh acceleration and cornering | 15 | 23 | 1.69 [1.22, 2.33] |
| Night driving | 15 | 16 | 1.44 [1.02, 2.04] |
| Phone use | 15 | 13 | 1.34 [0.95, 1.88] |

Speeding matters more than the hand weights assume, and harsh braking matters less. The intervals are wide (481 claims), and harsh braking's interval includes 1, so the data cannot say it has no effect. This is also partly an artefact of the generator, where braking was built as a noisier signal of risk than the other behaviours, so it should not be read as a real-world finding. The weights were **not** retuned after seeing these results, because tuning to the validation data would make later validation meaningless. With real data, the next step would be to fit a model on a training period and test it on a later one.

**4. The score is not just a proxy for age or region**

- Mean scores differ by group: 56.7 for ages 18-24 against 70.9 for 50-64, and 64.9 in urban areas against 68.5 in rural. This is expected here because the generator makes younger drivers riskier.
- The claim-rate multiplier per +10 score points is 0.80 [0.76, 0.83] on its own and 0.80 [0.77, 0.84] after controlling for age band and region. The score keeps its predictive power within demographic groups.
- Limitation: young drivers score about 14 points lower on average, so they would meet reward goals less often. A real programme would need a proper fairness review.

## Reward designs and assumptions

Run with `python src/simulate_rewards.py`. Three designs are compared with a no-rewards control over 26 weeks. All behaviour parameters sit in one labelled block at the top of the script.

| Design | Mechanics |
|---|---|
| **A, flat cashback** | R10 per week when the weekly score is 80 or higher |
| **B, streaks** | Same goal. The reward multiplier grows by 0.25 per consecutive week (capped at 2x) and a miss resets the streak. Motivation also dips for two weeks after losing a streak of four or more weeks |
| **C, tiers and leaderboard** | Weekly reward by tier (bronze 65+ R5, silver 75+ R10, gold 85+ R15) plus a R50 monthly bonus for the top 10% of drivers |
| **Control** | No rewards. Drivers still make some organic effort (half their engagement propensity) |

**How behaviour is modelled (all assumptions)**

1. Each week a design-specific "pull" shifts the log-odds that a driver makes an effort. Pull is stronger when the goal is within reach (goal-gradient), grows with streak length in B, and in C depends on closeness to the next tier and on leaderboard percentile.
2. Effort raises the driver's underlying score by a random gain that is larger when there is more room to improve. Accumulated gains decay by 10% a week.
3. The observed weekly score is the underlying score plus noise (sd 6 points), so goals are partly hit by luck.
4. Estimated claims saving is the drop in expected claims cost versus the control, using the step 3 relationship (0.80 times the claim rate per +10 points).

The simulation uses identical random draws across designs, so differences between designs come from the design and not from luck.

## Results

**Base scenario (26 weeks, per driver)**

| | A: Flat | B: Streaks | C: Tiers + leaderboard |
|---|---|---|---|
| Weekly effort rate (control 0.20) | 0.33 | 0.40 | 0.38 |
| Average score uplift (points) | 0.80 | 0.99 | 1.20 |
| Reward cost | R83 | R133 | R200 |
| Estimated claims saving | R38 | R45 | R60 |
| **Net (saving minus cost)** | **-R45** | **-R88** | **-R140** |
| Saving per R1 of reward | 0.46 | 0.34 | 0.30 |
| Uplift needed to break even (points) | 1.7 | 3.0 | 4.0 |
| Uplift, lowest third of baseline scores | 0.5 | 0.5 | 1.1 |

Charts: `report/reward_designs.png`. Full tables: `report/reward_design_summary.csv` and `report/reward_sensitivity.csv`.

**What the simulation suggests (under these assumptions)**

- **Claims savings alone do not pay for the rewards.** Every design costs more than the estimated saving in the base scenario. Rewards would need to be justified by other benefits such as retention, acquisition or data, which this simulation does not model.
- **Flat cashback is the most cost-efficient**, returning R0.46 of saving per R1 of reward against R0.30 to R0.34 for the others. B and C raise engagement and uplift more, but their extra cost outweighs the extra saving.
- **Tiers reach the riskiest drivers better.** The lowest third of baseline scores gains about 1.1 points under C but only 0.5 under A and B, because a bronze tier at 65 is within reach where a goal of 80 is not. This follows from the goal-gradient assumption, so treat it as a hypothesis to test.
- **Most reward money goes to drivers who were already safe.** Results by baseline score third (`sql/08_economics_by_baseline_third.sql`): the highest third receives R206 (A) to R417 (C) per driver in rewards for savings of only R27 to R44. The lowest third is net positive under every design (+R34 under A and B, +R54 under C), because they rarely hit the goal, so they cost little, while a point of improvement is worth more on a riskier driver. The overall negative net is therefore driven by paying for behaviour that would largely happen anyway. This points to rewarding improvement rather than level, or targeting rewards at riskier drivers, which is worth testing.
- **Streaks barely matter here.** With about one in three weeks meeting the goal, long streaks are rare, so the streak-loss assumption changes B's net result by less than R1.

**Sensitivity** (9 combinations of driver responsiveness and effect size per week of effort)

- A ranks first on net benefit in all 9, so the ranking is stable.
- The conclusion that rewards do not pay for themselves is **not** stable. A turns positive in 2 of the 9 scenarios (when drivers are 1.5 times more responsive and each week of effort is worth at least twice as much). B and C stay negative in all 9.
- The result therefore hinges on how much effort translates into a better score, which real experiments would have to measure.

**Simulation noise** across 10 random seeds is small (standard deviation under R1 per driver), so the differences between designs are not a sampling artefact. Model uncertainty, as shown by the sensitivity table, is far larger.

## SQL and dashboard data

Step 5 loads the simulation outputs into SQLite and runs eight queries (window functions, CTEs, joins and conditional aggregation). `run_sql.py` exports each result to `dashboard/data/` so Power BI can import the CSVs directly.

| Query | Dashboard page | Content |
|---|---|---|
| `01_portfolio_score_bands` | 1. Portfolio | Drivers, claims and frequency by score band |
| `02_score_deciles` | 1. Portfolio | Claim frequency by score decile |
| `03_segment_check` | 1. Portfolio | Score and claims by age band and region |
| `04_weekly_engagement` | 2. Engagement, 3. Economics | Effort rate, goal hit rate, rewards and cumulative cost by design and week |
| `05_streak_distribution` | 2. Engagement | Streak lengths over time (design B) |
| `06_tier_distribution` | 2. Engagement | Tier mix over time (design C) |
| `07_design_economics` | 3. Economics | Reward cost vs estimated claims saving by design |
| `08_economics_by_baseline_third` | 3. Economics | Who benefits: results by baseline score third |

**Testing:** `tests/test_sql_reconciliation.py` compares the SQL results with the independent Python calculations from steps 3 and 4 (deciles, weekly metrics, design economics) and checks completeness (every driver present in every week and design, shares sum to one, the control pays nothing). A wrong join or denominator would otherwise put wrong numbers on the dashboard without any error.

**Segment view:** only 11% of drivers aged 18 to 24 score 80 or higher, against 34% of those aged 50 to 64, so goal-based rewards would be claimed far less often by young drivers. In this synthetic data that follows from the built-in risk gap, but it is the kind of effect a real programme would need to review for fairness.

## Limitations

- **Synthetic data.** Relationships between behaviour and claims are built in by the generator, so real-world strength and shape may differ.
- **Assumed behaviour response.** The simulation cannot show which reward design works in practice. It shows how conclusions depend on the assumptions.
- **No causal claims.** Estimated claims savings assume that improving the score reduces risk. That is an assumption here, not a result.
- **Fairness.** Even an explainable score can act as a proxy for protected or sensitive characteristics. See the validation section for the checks done, and note they are limited.
- **Simplified behaviours.** Real telematics data includes road type, speed limits, weather and context that this simulation does not model.

## Next steps with real data

- Replace synthetic trips with real telematics and test whether the score still separates claims risk.
- Fit a generalised linear model (frequency and severity) instead of hand-set weights.
- Run an experiment or quasi-experiment to measure the actual effect of reward designs on behaviour.
- Review fairness across age, region and vehicle type with real distributions.