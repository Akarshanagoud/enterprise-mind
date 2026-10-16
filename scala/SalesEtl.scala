// Scala Spark implementation of the ETL contract (see src/emind/etl/pipeline.py).
// Build: sbt package   Run: spark-submit --class SalesEtl target/scala-2.12/sales-etl.jar in.csv out/
import org.apache.spark.sql.{SparkSession, functions => F}

object SalesEtl {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder.appName("emind-sales-etl").getOrCreate()
    val raw = spark.read.option("header", "true").csv(args(0))
      .withColumn("units", F.col("units").cast("int"))
      .withColumn("unit_price", F.col("unit_price").cast("double"))
      .withColumn("order_date", F.to_date(F.col("order_date")))

    val valid = F.col("order_id").isNotNull && F.col("order_date").isNotNull &&
      F.col("units") > 0 && F.col("unit_price") > 0 && F.col("region").isNotNull

    raw.filter(valid).dropDuplicates("order_id")
      .withColumn("revenue", F.round(F.col("units") * F.col("unit_price"), 2))
      .withColumn("month", F.date_format(F.col("order_date"), "yyyy-MM"))
      .write.mode("overwrite").partitionBy("month").parquet(s"${args(1)}/sales")
    raw.filter(!valid).write.mode("overwrite").json(s"${args(1)}/quarantine")
    spark.stop()
  }
}
