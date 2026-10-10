# FILE: dags/training_pipeline.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import sys
import os

# Add src to path so Airflow can find the project modules. This must point
# outside /opt/airflow/dags: Airflow scans that directory for DAG files, so
# mounting src inside it makes Airflow try to parse every project module as a
# DAG and report import errors.
sys.path.append(os.getenv("AEROPREDICT_SRC", "/opt/airflow/src"))


def retrain_model(**context):
    """
    Run training and return only a JSON-serializable summary for XCom.

    The import is deliberately inside the function. A module-level import of
    train_model pulls in torch, and if that import fails the DAG file fails to
    parse and the DAG silently disappears from the Airflow UI. Importing lazily
    means a dependency problem surfaces as a failed task with a traceback
    instead of a missing DAG.
    """
    from train_model import train

    model = train(
        data_path=os.getenv("AEROPREDICT_DATA", "/opt/airflow/data/train_FD001.txt")
    )
    return {
        "status": "trained",
        "parameters": sum(p.numel() for p in model.parameters()),
    }


def generate_report(**context):
    """Generate the GenAI maintenance report. Imported lazily for the same reason."""
    from rag_inference import generate_maintenance_report

    rul = context.get("rul_prediction", 23)
    report = generate_maintenance_report(rul)
    return {"status": "reported", "rul": rul, "report_chars": len(report or "")}


# Default Arguments
default_args = {
    "owner": "nishat",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Define the DAG (The Pipeline)
with DAG(
    dag_id="aeropredict_continuous_learning",
    default_args=default_args,
    description="Weekly Retraining + GenAI Reporting",
    start_date=datetime(2026, 1, 1),
    schedule_interval="@weekly",  # Runs every Sunday
    catchup=False,
) as dag:
    # Task 1: Retrain the Model
    train_task = PythonOperator(
        task_id="retrain_lstm_model",
        python_callable=retrain_model,
    )

    # Task 2: Generate the maintenance report for a simulated low-RUL engine
    report_task = PythonOperator(
        task_id="generate_maintenance_report",
        python_callable=generate_report,
        op_kwargs={"rul_prediction": 23},
    )

    # The Flow: Train -> Then Report
    train_task >> report_task
