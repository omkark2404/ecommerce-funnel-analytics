-- Data-quality checks on the loaded MySQL tables.
-- Every check must return 0. Run after sql/01_load_data.sql, e.g.
--   mysql ecommerce_analytics < sql/06_data_quality.sql
-- The same rules are implemented in Python in
-- src/funnel.py:integrity_problems, so a failure here should also
-- fail the pandas load — and vice versa.
USE ecommerce_analytics;

SELECT 'duplicate_user_id' AS check_name, COUNT(*) AS problems
FROM (SELECT user_id FROM users GROUP BY user_id HAVING COUNT(*) > 1) AS d
UNION ALL
SELECT 'duplicate_event_records' AS check_name, COUNT(*) AS problems
FROM (
    SELECT user_id, event_type, event_date
    FROM events
    GROUP BY user_id, event_type, event_date
    HAVING COUNT(*) > 1
) AS d
UNION ALL
SELECT 'events_for_unknown_users' AS check_name, COUNT(*) AS problems
FROM events AS e
LEFT JOIN users AS u ON u.user_id = e.user_id
WHERE u.user_id IS NULL
UNION ALL
SELECT 'events_before_signup' AS check_name, COUNT(*) AS problems
FROM events AS e
JOIN users AS u ON u.user_id = e.user_id
WHERE e.event_date < u.signup_date
UNION ALL
SELECT 'users_without_matching_signup_event' AS check_name, COUNT(*) AS problems
FROM users AS u
WHERE
    NOT EXISTS (
        SELECT 1
        FROM events AS e
        WHERE
            e.user_id = u.user_id
            AND e.event_type = 'signup'
            AND e.event_date = u.signup_date
    )
UNION ALL
SELECT 'null_values_in_users' AS check_name, COUNT(*) AS problems
FROM users
WHERE user_id IS NULL OR signup_date IS NULL OR country IS NULL
UNION ALL
SELECT 'null_values_in_events' AS check_name, COUNT(*) AS problems
FROM events
WHERE user_id IS NULL OR event_type IS NULL OR event_date IS NULL;
