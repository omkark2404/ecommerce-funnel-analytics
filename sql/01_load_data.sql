-- ======================================================================
-- 01_load_data.sql
-- .gitattributes keeps the checked-in CSV inputs on LF line endings.
-- ======================================================================

USE ecommerce_analytics;

-- Load Users Data
LOAD DATA INFILE '/var/lib/mysql-files/product_users_clean.csv'
INTO TABLE users
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 ROWS
(user_id, signup_date, country);

-- Load Events Data
LOAD DATA INFILE '/var/lib/mysql-files/product_events_clean.csv'
INTO TABLE events
FIELDS TERMINATED BY ','
ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 ROWS
(user_id, event_type, event_date);
