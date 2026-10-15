# FILE: dags/training_pipeline.py
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

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

    # /opt/airflow/src is a read-only bind mount, so the exported model and
    # scaler must go somewhere the container can write. The API reads them from
    # MODEL_PATH/SCALER_PATH, which default to this directory.
    model_dir = os.getenv("AEROPREDICT_MODEL_DIR", "/opt/airflow/models")
    model = train(
        data_path=os.getenv("AEROPREDICT_DATA", "/opt/airflow/data/train_FD001.txt"),
        model_dir=model_dir,
    )
    return {
        "status": "trained",
        "parameters": sum(p.numel() for p in model.parameters()),
        "model_dir": model_dir,
    }


def generate_report(**context):
    """Generate the GenAI maintenance report. Imported lazily for the same reason."""
    from rag_inference import generate_maintenance_report

    rul = context.get("rul_prediction", 23)
    report = generate_maintenance_report(rul)
    return {"status": "reported", "rul": rul, "report_chars": len(report or "")}


def reload_serving_model(**context):
    """Tell the serving API to pick up the freshly retrained checkpoint.

    Best-effort by design: if the API is not running (for example when only the
    DAG is being exercised) the task logs that and succeeds. The point of the
    task is to make the retrain -> serve handoff visible in the DAG graph and to
    fail loudly when the API *is* reachable but rejects the new weights.
    """
    import json
    import urllib.error
    import urllib.request

    url = os.getenv("AEROPREDICT_API_RELOAD_URL", "http://host.docker.internal:8000/admin/reload-model")

    try:
        request = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        return {"status": "reloaded", "response": payload}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        if exc.code == 403:
            # Reload is disabled by configuration, not broken.
            return {"status": "skipped", "reason": f"reload disabled: {detail}"}
        raise RuntimeError(f"API rejected the reload ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        return {"status": "skipped", "reason": f"API unreachable: {exc.reason}"}


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

    # Task 2: Hot-swap the served model so the retrain actually takes effect
    reload_task = PythonOperator(
        task_id="reload_serving_model",
        python_callable=reload_serving_model,
    )

    # Task 3: Generate the maintenance report for a simulated low-RUL engine
    report_task = PythonOperator(
        task_id="generate_maintenance_report",
        python_callable=generate_report,
        op_kwargs={"rul_prediction": 23},
    )

    # The Flow: Train -> Reload -> Report
    train_task >> reload_task >> report_task
