-- Feeds: Power BI page 3 (Economics) - who benefits? Results by baseline score third.
WITH ctrl AS (
    SELECT driver_id, avg_underlying_score AS ctrl_score
    FROM driver_design_outcomes WHERE design LIKE 'Control%'
)
SELECT o.design,
       o.baseline_third,
       COUNT(*)                                                         AS drivers,
       ROUND(AVG(o.avg_underlying_score - c.ctrl_score), 2)             AS avg_score_uplift_pts,
       ROUND(AVG(o.reward_cost_zar), 2)                                 AS reward_cost_per_driver,
       ROUND(AVG(o.claims_saving_zar), 2)                               AS claims_saving_per_driver,
       ROUND(AVG(o.claims_saving_zar) - AVG(o.reward_cost_zar), 2)      AS net_per_driver
FROM driver_design_outcomes o
JOIN ctrl c USING (driver_id)
WHERE o.design NOT LIKE 'Control%'
GROUP BY o.design, o.baseline_third
ORDER BY o.design,
         CASE o.baseline_third WHEN 'Lowest third' THEN 1 WHEN 'Middle third' THEN 2 ELSE 3 END;
