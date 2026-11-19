-- Feeds: Power BI page 3 (Economics) - reward cost vs estimated claims saving by design.
-- Savings are relative to the no-rewards control and rest on the assumption that
-- score improvements reduce claim risk by the step 3 relationship (about 0.80x per +10 points).
WITH ctrl AS (
    SELECT driver_id, avg_underlying_score AS ctrl_score
    FROM driver_design_outcomes WHERE design LIKE 'Control%'
)
SELECT o.design,
       COUNT(*)                                                         AS drivers,
       ROUND(AVG(o.avg_underlying_score - c.ctrl_score), 2)             AS avg_score_uplift_pts,
       ROUND(AVG(o.reward_cost_zar), 2)                                 AS reward_cost_per_driver,
       ROUND(AVG(o.claims_saving_zar), 2)                               AS claims_saving_per_driver,
       ROUND(AVG(o.claims_saving_zar) - AVG(o.reward_cost_zar), 2)      AS net_per_driver,
       ROUND(SUM(o.claims_saving_zar) / SUM(o.reward_cost_zar), 3)      AS saving_per_R1_reward,
       ROUND(SUM(o.reward_cost_zar), 0)                                 AS total_reward_cost,
       ROUND(SUM(o.claims_saving_zar), 0)                               AS total_claims_saving
FROM driver_design_outcomes o
JOIN ctrl c USING (driver_id)
WHERE o.design NOT LIKE 'Control%'
GROUP BY o.design
ORDER BY o.design;
