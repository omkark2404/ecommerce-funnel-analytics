-- Days from signup to first purchase among observed purchasers. This is a
-- distribution of buyers, not a conversion rate: newer signups have had less
-- time to purchase.
USE ecommerce_analytics;

WITH signup_stage AS (
    SELECT user_id, MIN(event_date) AS signup_date
    FROM events
    WHERE event_type = 'signup'
    GROUP BY user_id
),

first_purchase AS (
    SELECT
        s.user_id,
        DATEDIFF(MIN(e.event_date), s.signup_date) AS days_to_purchase
    FROM signup_stage AS s
    JOIN events AS e
        ON
            e.user_id = s.user_id
            AND e.event_type = 'purchase'
            AND e.event_date >= s.signup_date
    GROUP BY s.user_id, s.signup_date
),

daily AS (
    SELECT
        days_to_purchase,
        COUNT(*) AS observed_purchasers
    FROM first_purchase
    GROUP BY days_to_purchase
)

SELECT
    days_to_purchase,
    observed_purchasers,
    ROUND(
        observed_purchasers * 100.0 / SUM(observed_purchasers) OVER (),
        2
    ) AS pct_of_observed_purchasers,
    ROUND(
        SUM(observed_purchasers) OVER (ORDER BY days_to_purchase) * 100.0
        / SUM(observed_purchasers) OVER (),
        2
    ) AS cumulative_pct
FROM daily
ORDER BY days_to_purchase;
