-- Run once in a Snowsight worksheet (as ACCOUNTADMIN or a role that can create these objects).
CREATE WAREHOUSE IF NOT EXISTS ANALYTICS_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
CREATE DATABASE IF NOT EXISTS BROKERAGE;
CREATE SCHEMA IF NOT EXISTS BROKERAGE.ANALYTICS;
-- Tables are created by `python sync.py` from sql/schema.sql.

-- Handy queries to show in Snowsight during the demo:
-- SELECT status, COUNT(*) AS leads FROM BROKERAGE.ANALYTICS.LEADS GROUP BY status ORDER BY leads DESC;
-- SELECT DATE_TRUNC('month', updated_at) AS month, SUM(commission) AS revenue
--   FROM BROKERAGE.ANALYTICS.LEADS WHERE status = 'Leased' GROUP BY 1 ORDER BY 1;
