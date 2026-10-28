-- Account-level signup-to-purchase conversion. Differences are descriptive;
-- this query does not establish statistical significance or causality.
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
