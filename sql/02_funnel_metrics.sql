-- User-level funnel. Date-only data means same-day events are treated as
-- eligible in the listed stage order; the source cannot prove within-day order.
--
-- Each stage is a LEFT JOIN on the previous stage's date, so a user
-- counts at a stage only if they reached the previous one first.
-- This replaces the earlier version's five correlated subqueries
-- with the same logic in join form; the output table is unchanged.
USE ecommerce_analytics;

WITH signup_stage AS (
    SELECT user_id, MIN(event_date) AS signup_date
    FROM events
    WHERE event_type = 'signup'
    GROUP BY user_id
),

login_stage AS (
    SELECT
        s.user_id,
        s.signup_date,
        MIN(e.event_date) AS login_date
    FROM signup_stage AS s
    LEFT JOIN events AS e
        ON
            e.user_id = s.user_id
            AND e.event_type = 'login'
            AND e.event_date >= s.signup_date
    GROUP BY s.user_id, s.signup_date
),

view_stage AS (
    SELECT
        l.user_id,
        l.signup_date,
        l.login_date,
        MIN(e.event_date) AS view_date
    FROM login_stage AS l
    LEFT JOIN events AS e
        ON
            e.user_id = l.user_id
            AND e.event_type = 'view_product'
            AND e.event_date >= l.login_date
    GROUP BY l.user_id, l.signup_date, l.login_date
),

cart_stage AS (
    SELECT
        v.user_id,
        v.signup_date,
        v.login_date,
        v.view_date,
        MIN(e.event_date) AS cart_date
    FROM view_stage AS v
    LEFT JOIN events AS e
        ON
            e.user_id = v.user_id
            AND e.event_type = 'add_to_cart'
            AND e.event_date >= v.view_date
    GROUP BY v.user_id, v.signup_date, v.login_date, v.view_date
),

purchase_stage AS (
    SELECT
        c.user_id,
        c.signup_date,
        c.login_date,
        c.view_date,
        c.cart_date,
        MIN(e.event_date) AS purchase_date
    FROM cart_stage AS c
    LEFT JOIN events AS e
        ON
            e.user_id = c.user_id
            AND e.event_type = 'purchase'
            AND e.event_date >= c.cart_date
    GROUP BY
        c.user_id, c.signup_date, c.login_date, c.view_date, c.cart_date
),

funnel_counts AS (
    SELECT
        COUNT(*) AS signups,
        COUNT(login_date) AS logins,
        COUNT(view_date) AS product_views,
        COUNT(cart_date) AS cart_adds,
        COUNT(purchase_date) AS purchases
    FROM purchase_stage
)

SELECT
    '1_signup' AS stage,
    signups AS users,
    NULL AS previous_stage_users,
    100.00 AS step_conversion_pct,
    100.00 AS cumulative_conversion_pct
FROM funnel_counts
UNION ALL
SELECT
    '2_login' AS stage,
    logins AS users,
    signups AS previous_stage_users,
    ROUND(logins * 100.0 / NULLIF(signups, 0), 2) AS step_conversion_pct,
    ROUND(logins * 100.0 / NULLIF(signups, 0), 2) AS cumulative_conversion_pct
FROM funnel_counts
UNION ALL
SELECT
    '3_view_product' AS stage,
    product_views AS users,
    logins AS previous_stage_users,
    ROUND(product_views * 100.0 / NULLIF(logins, 0), 2) AS step_conversion_pct,
    ROUND(product_views * 100.0 / NULLIF(signups, 0), 2)
        AS cumulative_conversion_pct
FROM funnel_counts
UNION ALL
SELECT
    '4_add_to_cart' AS stage,
    cart_adds AS users,
    product_views AS previous_stage_users,
    ROUND(cart_adds * 100.0 / NULLIF(product_views, 0), 2)
        AS step_conversion_pct,
    ROUND(cart_adds * 100.0 / NULLIF(signups, 0), 2)
        AS cumulative_conversion_pct
FROM funnel_counts
UNION ALL
SELECT
    '5_purchase' AS stage,
    purchases AS users,
    cart_adds AS previous_stage_users,
    ROUND(purchases * 100.0 / NULLIF(cart_adds, 0), 2) AS step_conversion_pct,
    ROUND(purchases * 100.0 / NULLIF(signups, 0), 2)
        AS cumulative_conversion_pct
FROM funnel_counts;
