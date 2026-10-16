-- T-SQL (SQL Server / Azure SQL / Synapse) reporting layer matching the SQLite `sales` schema.
CREATE OR ALTER VIEW dbo.vw_monthly_region_revenue AS
SELECT  region,
        [month],
        SUM(revenue)                                         AS revenue,
        SUM(units)                                           AS units,
        SUM(revenue) - LAG(SUM(revenue)) OVER (PARTITION BY region ORDER BY [month]) AS mom_delta,
        RANK() OVER (PARTITION BY [month] ORDER BY SUM(revenue) DESC)               AS region_rank
FROM    dbo.sales
GROUP BY region, [month];
GO

-- Idempotent CDC-style upsert (mirrors emind.etl.pipeline.run_etl)
CREATE OR ALTER PROCEDURE dbo.usp_upsert_sales @batch dbo.SalesTableType READONLY AS
BEGIN
    SET NOCOUNT ON;
    MERGE dbo.sales AS t
    USING @batch AS s ON t.order_id = s.order_id
    WHEN MATCHED AND t.row_hash <> s.row_hash THEN
        UPDATE SET order_date = s.order_date, region = s.region, product = s.product,
                   units = s.units, unit_price = s.unit_price, revenue = s.revenue, row_hash = s.row_hash
    WHEN NOT MATCHED THEN
        INSERT (order_id, order_date, [month], region, product, units, unit_price, revenue, row_hash)
        VALUES (s.order_id, s.order_date, s.[month], s.region, s.product, s.units, s.unit_price, s.revenue, s.row_hash);
END
