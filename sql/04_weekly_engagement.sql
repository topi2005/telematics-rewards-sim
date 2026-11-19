-- Feeds: Power BI pages 2 and 3 (Engagement, Economics) - weekly engagement and cost by design.
-- "engaged" = the driver made an effort that week. In the real world this would
-- not be observed directly; here it is a simulation output.
-- Streak columns are only meaningful for designs A and B (NULL otherwise).
SELECT design,
       week,
       COUNT(*)                                                         AS drivers,
       ROUND(AVG(engaged), 4)                                           AS effort_rate,
       ROUND(AVG(goal_met), 4)                                          AS goal_hit_rate,
       ROUND(AVG(weekly_score), 2)                                      AS mean_observed_score,
       ROUND(AVG(CASE WHEN reward_zar > 0 THEN 1.0 ELSE 0 END), 4)      AS share_rewarded,
       ROUND(SUM(reward_zar), 2)                                        AS reward_cost_total,
       ROUND(SUM(SUM(reward_zar)) OVER (PARTITION BY design ORDER BY week)
             / COUNT(*), 2)                                             AS cumulative_reward_per_driver,
       CASE WHEN design LIKE 'A:%' OR design LIKE 'B:%'
            THEN ROUND(AVG(streak_length), 3) END                       AS avg_streak_length,
       CASE WHEN design LIKE 'A:%' OR design LIKE 'B:%'
            THEN ROUND(AVG(CASE WHEN streak_length >= 3 THEN 1.0 ELSE 0 END), 4) END
                                                                        AS share_with_3plus_streak
FROM reward_events
GROUP BY design, week
ORDER BY design, week;
