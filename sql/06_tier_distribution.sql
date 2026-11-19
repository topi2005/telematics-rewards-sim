-- Feeds: Power BI page 2 (Engagement) - tier mix over time for design C.
SELECT week,
       tier,
       CASE tier WHEN 'none' THEN 0 WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 ELSE 3 END AS tier_order,
       COUNT(*)                                                 AS drivers,
       ROUND(1.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY week), 4) AS share_of_drivers,
       ROUND(SUM(reward_zar), 2)                                AS reward_cost_total
FROM reward_events
WHERE design LIKE 'C:%'
GROUP BY week, tier
ORDER BY week, tier_order;
