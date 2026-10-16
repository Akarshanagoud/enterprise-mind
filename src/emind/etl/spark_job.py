"""PySpark implementation of the same ETL contract, for volumes beyond a single machine.

Run (free, local mode):  pip install pyspark && python -m emind.etl.spark_job data.csv out_dir
On a cluster the identical code runs on EMR / Databricks / Synapse Spark pools.
"""
from __future__ import annotations

import sys


def main(src: str, out: str) -> None:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = SparkSession.builder.appName("emind-sales-etl").getOrCreate()
    df = spark.read.option("header", True).csv(src)
    df = (df.withColumn("units", F.col("units").cast("int"))
            .withColumn("unit_price", F.col("unit_price").cast("double"))
            .withColumn("order_date", F.to_date("order_date")))
    valid = (F.col("order_id").isNotNull() & F.col("order_date").isNotNull()
             & (F.col("units") > 0) & (F.col("unit_price") > 0) & F.col("region").isNotNull())
    good = df.filter(valid).dropDuplicates(["order_id"])
    bad = df.filter(~valid)
    good = (good.withColumn("month", F.date_format("order_date", "yyyy-MM"))
                .withColumn("revenue", F.round(F.col("units") * F.col("unit_price"), 2)))
    good.write.mode("overwrite").partitionBy("month").parquet(f"{out}/sales")
    bad.write.mode("overwrite").json(f"{out}/quarantine")
    good.groupBy("region").agg(F.sum("revenue").alias("revenue")).show()
    spark.stop()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
