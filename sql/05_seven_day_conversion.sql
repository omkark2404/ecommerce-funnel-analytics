-- Seven-day signup-to-purchase conversion among users with a complete
-- seven-day observation window. The window is derived from the latest event.
USE ecommerce_analytics;

WITH observation_window AS (
    SELECT MAX(event_date) AS observed_through
    FROM events
),

mature_users AS (
    SELECT
        u.user_id,
        u.signup_date,
        w.observed_through,
        DATE_SUB(w.observed_through, INTERVAL 7 DAY) AS latest_mature_signup
    FROM users AS u
    CROSS JOIN observation_window AS w
    WHERE u.signup_date <= DATE_SUB(w.observed_through, INTERVAL 7 DAY)
),

qualified_purchases_in_window AS (
    SELECT
        m.user_id,
        MIN(e.event_date) AS purchase_date
    FROM mature_users AS m
    JOIN events AS e
        ON
            e.user_id = m.user_id
            AND e.event_type = 'purchase'
            AND e.event_date >= m.signup_date
            AND e.event_date <= DATE_ADD(m.signup_date, INTERVAL 7 DAY)
    GROUP BY m.user_id
)

SELECT
    MAX(m.latest_mature_signup) AS signup_cutoff,
    MAX(m.observed_through) AS observed_through,
    7 AS follow_up_days,
    COUNT(*) AS eligible_users,
    SUM(
        CASE
            WHEN p.purchase_date IS NOT NULL
                THEN 1
            ELSE 0
        END
    ) AS purchasers_within_7_days,
    ROUND(
        SUM(
            CASE
                WHEN p.purchase_date IS NOT NULL
                    THEN 1
                ELSE 0
            END
        ) * 100.0 / NULLIF(COUNT(*), 0),
        2
    ) AS conversion_rate_pct
FROM mature_users AS m
LEFT JOIN qualified_purchases_in_window AS p ON p.user_id = m.user_id;
