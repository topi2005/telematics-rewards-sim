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
| 4 | Rewards simulation | To do |
| 5 | SQL aggregations | To do |
| 6 | Power BI dashboard | To do |
| 7 | One-page summary | To do |

Sections marked **[fill in]** are completed after the relevant step, using your real results. Delete this table and the markers when the project is finished.

## Purpose

Usage-based motor insurance rewards drivers for driving well. This project explores three questions:

1. Can an explainable behaviour score separate lower-risk drivers from higher-risk ones?
2. Which reward mechanics (flat goals, streaks, tiers) keep drivers engaged at a cost that claims savings could justify?
3. How sensitive are those conclusions to assumptions about how drivers respond?

Because the data is synthetic, the project demonstrates method and reasoning. It does not make claims about how any real insurer or programme performs.

## Key findings

**[fill in after step 6]** 3 to 4 bullets with real numbers, for example:

- Claim frequency in the lowest score decile was X times that of the highest. (Step 3 gives the first numbers: the lowest quintile has 3.6 times the highest quintile's frequency.)
- Reward design B (streaks) achieved the highest engagement but cost Y% more than design A.
- The ranking of designs changed when driver responsiveness was reduced, so the conclusion depends on that assumption.

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
│   └── simulate_rewards.py  # step 4
├── notebooks/
├── dashboard/             # .pbix and screenshots
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

**[fill in after step 4]** The three designs to be compared:

- **A, flat cashback:** a fixed weekly reward when the weekly score meets the goal
- **B, streaks:** the reward multiplier grows with consecutive weeks meeting the goal and resets on a miss
- **C, tiers and leaderboard:** bronze, silver and gold levels with a monthly rank bonus

The behaviour model (how likely a driver is to improve under each design) is an assumption, not an empirical finding. Document each assumption here.

## Results

**[fill in after steps 4 to 6]** Dashboard screenshots and a table of engagement, average score change, reward cost and estimated claims saving by design, plus the sensitivity check on driver responsiveness.

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