-- Definitions: docs/KPI_DEFINITIONS.md.
-- Account-level signup-to-purchase conversion. Differences are descriptive;
-- this query does not establish statistical significance or causality.
-- Definition: any purchase on or after signup counts, even if the user
-- skipped an intermediate stage (unlike sql/02, which requires stage
-- order). In this sample the two agree — no user skips a stage — so
-- the result is the same either way; see notebook §1.
USE ecommerce_analytics;

WITH user_conversion AS (
    SELECT
        u.user_id,
        u.country,
        MAX(
            CASE
                WHEN
                    e.event_type = 'purchase'
                    AND e.event_date >= u.signup_date
                    THEN 1
                ELSE 0
            END
        ) AS has_purchased_after_signup
    FROM users AS u
    LEFT JOIN events AS e ON e.user_id = u.user_id
    WHERE u.signup_date IS NOT NULL
    GROUP BY u.user_id, u.country
)

SELECT
    country,
    COUNT(*) AS total_users,
    SUM(has_purchased_after_signup) AS valid_purchases,
    ROUND(
        SUM(has_purchased_after_signup) * 100.0 / NULLIF(COUNT(*), 0),
        2
    ) AS conversion_rate_pct
FROM user_conversion
GROUP BY country
ORDER BY conversion_rate_pct DESC, country ASC;
