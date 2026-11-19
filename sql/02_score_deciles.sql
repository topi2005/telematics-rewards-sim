-- Feeds: Power BI page 1 (Portfolio) - claim frequency by score decile.
-- Decile 1 = lowest scores (riskiest). Ties broken by driver_id so deciles
-- are exactly equal in size. Should reconcile with report/decile_table.csv.
WITH params AS (SELECT 26.0 AS weeks),
base AS (
    SELECT s.driver_id,
           s.score,
           s.km / (SELECT AVG(km) FROM drivers_scored)
               * (SELECT weeks FROM params) / 52.0          AS exposure,
           COALESCE(c.n_claims, 0)                          AS n_claims
    FROM drivers_scored s
    LEFT JOIN (SELECT driver_id, COUNT(*) AS n_claims
               FROM claims GROUP BY driver_id) c USING (driver_id)
),
ranked AS (
    SELECT *, NTILE(10) OVER (ORDER BY score, driver_id) AS decile FROM base
)
SELECT decile,
       COUNT(*)                                         AS drivers,
       ROUND(MIN(score), 1)                             AS min_score,
       ROUND(MAX(score), 1)                             AS max_score,
       SUM(n_claims)                                    AS claims,
       ROUND(SUM(exposure), 2)                          AS exposure_driver_years,
       ROUND(SUM(n_claims) / SUM(exposure), 4)          AS claims_per_driver_year
FROM ranked
GROUP BY decile
ORDER BY decile;
