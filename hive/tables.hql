-- Hive / Spark-SQL external tables over the partitioned Parquet written by spark_job.py / SalesEtl.scala
CREATE EXTERNAL TABLE IF NOT EXISTS sales_lake (
  order_id STRING, order_date DATE, region STRING, product STRING,
  units INT, unit_price DOUBLE, revenue DOUBLE)
PARTITIONED BY (month STRING)
STORED AS PARQUET
LOCATION 'hdfs:///warehouse/emind/sales';
MSCK REPAIR TABLE sales_lake;

SELECT month, region, SUM(revenue) AS revenue
FROM sales_lake GROUP BY month, region ORDER BY month, revenue DESC;
