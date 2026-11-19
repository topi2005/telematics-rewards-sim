-- Feeds: Power BI page 1 (Portfolio) - score distribution and claim frequency by risk band.
-- Exposure = distance driven relative to the average driver, scaled to the
-- observation window (26 weeks). Frequency = claims per average driver-year.
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
banded AS (
    SELECT *,
           CASE WHEN score < 50 THEN 1
                WHEN score < 65 THEN 2
                WHEN score < 80 THEN 3
                WHEN score < 90 THEN 4
                ELSE 5 END AS band_order
    FROM base
)
SELECT band_order,
       CASE band_order WHEN 1 THEN 'Below 50' WHEN 2 THEN '50 to 64' WHEN 3 THEN '65 to 79'
                       WHEN 4 THEN '80 to 89' ELSE '90 and above' END       AS score_band,
       COUNT(*)                                                             AS drivers,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM banded), 1)           AS pct_of_drivers,
       ROUND(AVG(score), 1)                                                 AS avg_score,
       SUM(n_claims)                                                        AS claims,
       ROUND(SUM(exposure), 1)                                              AS exposure_driver_years,
       ROUND(SUM(n_claims) / SUM(exposure), 4)                              AS claims_per_driver_year,
       ROUND((SUM(n_claims) / SUM(exposure))
             / (SELECT SUM(n_claims) / SUM(exposure) FROM banded), 2)       AS relative_to_portfolio
FROM banded
GROUP BY band_order
ORDER BY band_order;
