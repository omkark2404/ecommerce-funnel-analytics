-- ======================================================================
-- 01_load_data.sql — repeatable, safe reload of the demonstration data.
--
-- The old version deleted the live tables and then ran LOAD DATA
-- directly into them: a failure partway through (a bad date, a
-- missing file, a duplicate key) left the database partially
-- populated, and the analysis queries would then run on incomplete
-- data that still looked valid.
--
-- This version loads into staging tables first and only swaps them
-- into place when validation passes:
--
--   1. LOAD DATA fills users_staging / events_staging. LOAD DATA is
--      not transactional, so it must never touch the live tables.
--   2. The staging validation guard counts every integrity problem
--      (the same rules as sql/06_data_quality.sql, plus nulls and
--      unknown event types). If the count is not zero, an INSERT
--      into load_guard violates its CHECK constraint and the mysql
--      client aborts the script — before any live table is modified.
--      The staging tables are left behind for inspection; the next
--      run drops them first.
--   3. The swap (DELETE + INSERT ... SELECT) runs as a single
--      InnoDB transaction. If it fails, the uncommitted transaction
--      is rolled back when the client connection ends, and the live
--      tables keep their previous contents.
--
-- .gitattributes keeps the checked-in CSV inputs on LF line endings.
-- This is safe for this demonstration database; do not run it against
-- a production database without a backup.
-- ======================================================================

USE ecommerce_analytics;

-- Staging tables: same columns, deliberately no primary key, foreign
-- key or CHECK constraints, so that bad input is caught by the
-- explicit validation below (with a clear problem count) instead of
-- failing inside LOAD DATA with a cryptic error.
DROP TABLE IF EXISTS users_staging;
DROP TABLE IF EXISTS events_staging;
CREATE TABLE users_staging (
    user_id INT NOT NULL,
    signup_date DATE NOT NULL,
    country VARCHAR(100) NOT NULL
);
CREATE TABLE events_staging (
    user_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_date DATE NOT NULL
);

-- Load Users Data (into staging only)
LOAD DATA INFILE '/var/lib/mysql-files/product_users_clean.csv'
INTO TABLE users_staging
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 ROWS
(user_id, signup_date, country);

-- Load Events Data (into staging only)
LOAD DATA INFILE '/var/lib/mysql-files/product_events_clean.csv'
INTO TABLE events_staging
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 ROWS
(user_id, event_type, event_date);

-- BEGIN staging validation guard
-- Count every integrity problem in the staging tables. The guard
-- INSERT below inserts ok = 1 only when the count is zero; otherwise
-- it inserts ok = 0, which violates the CHECK constraint and aborts
-- the script before the live tables are touched.
SELECT COALESCE(SUM(problems), 0) INTO @staging_problems
FROM (
    SELECT COUNT(*) AS problems
    FROM users_staging
    WHERE user_id IS NULL OR signup_date IS NULL OR country IS NULL
    UNION ALL
    SELECT COUNT(*)
    FROM events_staging
    WHERE user_id IS NULL OR event_type IS NULL OR event_date IS NULL
    UNION ALL
    SELECT COUNT(*)
    FROM (
        SELECT user_id
        FROM users_staging
        GROUP BY user_id
        HAVING COUNT(*) > 1
    ) AS d
    UNION ALL
    SELECT COUNT(*)
    FROM (
        SELECT user_id, event_type, event_date
        FROM events_staging
        GROUP BY user_id, event_type, event_date
        HAVING COUNT(*) > 1
    ) AS d
    UNION ALL
    SELECT COUNT(*)
    FROM events_staging
    WHERE
        event_type NOT IN (
            'signup', 'login', 'view_product', 'add_to_cart', 'purchase'
        )
    UNION ALL
    SELECT COUNT(*)
    FROM events_staging AS e
    LEFT JOIN users_staging AS u ON u.user_id = e.user_id
    WHERE u.user_id IS NULL
    UNION ALL
    SELECT COUNT(*)
    FROM events_staging AS e
    JOIN users_staging AS u ON u.user_id = e.user_id
    WHERE e.event_date < u.signup_date
    UNION ALL
    SELECT COUNT(*)
    FROM users_staging AS u
    WHERE
        NOT EXISTS (
            SELECT 1
            FROM events_staging AS e
            WHERE
                e.user_id = u.user_id
                AND e.event_type = 'signup'
                AND e.event_date = u.signup_date
        )
) AS all_checks;

DROP TABLE IF EXISTS load_guard;
CREATE TABLE load_guard (
    ok INT NOT NULL,
    CONSTRAINT chk_load_guard_ok CHECK (ok = 1)
);
INSERT INTO load_guard (ok)
VALUES (CASE WHEN @staging_problems = 0 THEN 1 ELSE 0 END);
DROP TABLE load_guard;
-- END staging validation guard

-- BEGIN swap
-- Replace the live tables in one transaction. DELETE (not TRUNCATE)
-- is used because MySQL refuses to truncate a table referenced by a
-- foreign key — users is referenced by events — even when the child
-- table is empty.
START TRANSACTION;
DELETE FROM events;
DELETE FROM users;
INSERT INTO users
SELECT * FROM users_staging;
INSERT INTO events
SELECT * FROM events_staging;
COMMIT;
-- END swap

DROP TABLE IF EXISTS users_staging;
DROP TABLE IF EXISTS events_staging;
