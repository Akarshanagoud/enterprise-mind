"""Apache Airflow DAG: nightly ETL -> data-quality gate -> re-index -> RAG regression eval."""
from datetime import datetime, timedelta

from airflow.decorators import dag, task


@dag(schedule="0 2 * * *", start_date=datetime(2025, 1, 1), catchup=False,
     default_args={"retries": 2, "retry_delay": timedelta(minutes=5)}, tags=["emind"])
def emind_daily():
    @task
    def etl(csv_path: str = "/data/incoming/sales.csv") -> dict:
        from emind.config import Settings
        from emind.etl.pipeline import run_etl

        return run_etl(csv_path, Settings.from_env().warehouse_path).to_dict()

    @task
    def quality_gate(report: dict) -> None:
        bad = report["quarantined"] / max(report["rows_in"], 1)
        if bad > 0.05:
            raise ValueError(f"quarantine rate {bad:.1%} exceeds 5% threshold")

    @task
    def reindex_and_eval() -> dict:
        from pathlib import Path

        from emind.api import create_app
        from emind.evaluation import evaluate

        res = evaluate(create_app().state.rag, Path("/app/data/golden.jsonl"))
        if (res["answer_accuracy"] or 0) < 0.9 or (res["abstain_accuracy"] or 0) < 1.0:
            raise ValueError(f"RAG regression: {res}")
        return res

    quality_gate(etl()) >> reindex_and_eval()


emind_daily()
