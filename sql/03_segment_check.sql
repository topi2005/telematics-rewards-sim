-- Feeds: Power BI page 1 (Portfolio) - fairness view: does the score differ by age band or region?
WITH params AS (SELECT 26.0 AS weeks),
base AS (
    SELECT s.driver_id, d.age_band, d.region, s.score,
           s.km / (SELECT AVG(km) FROM drivers_scored)
               * (SELECT weeks FROM params) / 52.0          AS exposure,
           COALESCE(c.n_claims, 0)                          AS n_claims
    FROM drivers_scored s
    JOIN drivers d USING (driver_id)
    LEFT JOIN (SELECT driver_id, COUNT(*) AS n_claims
               FROM claims GROUP BY driver_id) c USING (driver_id)
),
segments AS (
    SELECT 'Age band' AS segment_type, age_band AS segment, * FROM base
    UNION ALL
    SELECT 'Region'   AS segment_type, region   AS segment, * FROM base
)
SELECT segment_type,
       segment,
       COUNT(*)                                                     AS drivers,
       ROUND(AVG(score), 1)                                         AS avg_score,
       ROUND(100.0 * AVG(CASE WHEN score >= 80 THEN 1.0 ELSE 0 END), 1) AS pct_scoring_80_plus,
       SUM(n_claims)                                                AS claims,
       ROUND(SUM(n_claims) / SUM(exposure), 4)                      AS claims_per_driver_year
FROM segments
GROUP BY segment_type, segment
ORDER BY segment_type, segment;
