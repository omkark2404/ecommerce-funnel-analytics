-- User-level funnel. Date-only data means same-day events are treated as
-- eligible in the listed stage order; the source cannot prove within-day order.
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
        (
            SELECT MIN(e.event_date)
            FROM events AS e
            WHERE
                e.user_id = s.user_id
                AND e.event_type = 'login'
                AND e.event_date >= s.signup_date
        ) AS login_date
    FROM signup_stage AS s
),

view_stage AS (
    SELECT
        l.user_id,
        l.signup_date,
        l.login_date,
        (
            SELECT MIN(e.event_date)
            FROM events AS e
            WHERE
                e.user_id = l.user_id
                AND e.event_type = 'view_product'
                AND l.login_date IS NOT NULL
                AND e.event_date >= l.login_date
        ) AS view_date
    FROM login_stage AS l
),

cart_stage AS (
    SELECT
        v.user_id,
        v.signup_date,
        v.login_date,
        v.view_date,
        (
            SELECT MIN(e.event_date)
            FROM events AS e
            WHERE
                e.user_id = v.user_id
                AND e.event_type = 'add_to_cart'
                AND v.view_date IS NOT NULL
                AND e.event_date >= v.view_date
        ) AS cart_date
    FROM view_stage AS v
),

purchase_stage AS (
    SELECT
        c.user_id,
        c.signup_date,
        c.login_date,
        c.view_date,
        c.cart_date,
        (
            SELECT MIN(e.event_date)
            FROM events AS e
            WHERE
                e.user_id = c.user_id
                AND e.event_type = 'purchase'
                AND c.cart_date IS NOT NULL
                AND e.event_date >= c.cart_date
        ) AS purchase_date
    FROM cart_stage AS c
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
