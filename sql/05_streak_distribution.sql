-- Feeds: Power BI page 2 (Engagement) - streak lengths over time for design B.
WITH bucketed AS (
    SELECT week,
           CASE WHEN streak_length = 0 THEN 0
                WHEN streak_length = 1 THEN 1
                WHEN streak_length = 2 THEN 2
                WHEN streak_length = 3 THEN 3
                WHEN streak_length <= 5 THEN 4
                ELSE 5 END AS bucket_order
    FROM reward_events
    WHERE design LIKE 'B:%'
)
SELECT week,
       bucket_order,
       CASE bucket_order WHEN 0 THEN 'No streak' WHEN 1 THEN '1 week' WHEN 2 THEN '2 weeks'
                         WHEN 3 THEN '3 weeks' WHEN 4 THEN '4 to 5 weeks' ELSE '6+ weeks' END
                                                                AS streak_bucket,
       COUNT(*)                                                 AS drivers,
       ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY week), 4) AS share_of_drivers
FROM bucketed
GROUP BY week, bucket_order
ORDER BY week, bucket_order;
