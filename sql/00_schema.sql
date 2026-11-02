-- Idempotent schema creation for the bundled demonstration dataset.
CREATE DATABASE IF NOT EXISTS ecommerce_analytics;
USE ecommerce_analytics;

CREATE TABLE IF NOT EXISTS users (
    user_id INT NOT NULL PRIMARY KEY,
    signup_date DATE NOT NULL,
    country VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    user_id INT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_date DATE NOT NULL,
    CONSTRAINT chk_events_event_type
    CHECK (
        event_type IN (
            'signup', 'login', 'view_product', 'add_to_cart', 'purchase'
        )
    ),
    CONSTRAINT fk_events_user
    FOREIGN KEY (user_id) REFERENCES users (user_id),
    INDEX idx_events_user_type_date (user_id, event_type, event_date)
);
-- No UNIQUE(user_id, event_type, event_date) is imposed here: repeated
-- events of the same type on different dates are legitimate in
-- clickstream data. Exact duplicate event records are detected by
-- sql/06_data_quality.sql and by src/funnel.py:integrity_problems,
-- instead of being silently accepted or rejected by the schema.
