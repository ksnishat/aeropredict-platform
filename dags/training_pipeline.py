# FILE: dags/training_pipeline.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import sys
import os

# Add src to path so Airflow can find your scripts
sys.path.append("/opt/airflow/dags/src")

# Import your functions
from train_model import train
from rag_inference import generate_maintenance_report

# Default Arguments
default_args = {
    'owner': 'nishat',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG (The Pipeline)
with DAG(
    dag_id='aeropredict_continuous_learning',
    default_args=default_args,
    description='Weekly Retraining + GenAI Reporting',
    start_date=datetime(2026, 1, 1),
    schedule_interval='@weekly', # Runs every Sunday
    catchup=False
) as dag:

    # Task 1: Retrain the Model (GPU)
    train_task = PythonOperator(
        task_id='retrain_lstm_model',
        python_callable=train
    )

    # Task 2: Generate Report (GenAI)
    # We simulate a "low RUL" detection to trigger the report
    report_task = PythonOperator(
        task_id='generate_maintenance_report',
        python_callable=generate_maintenance_report,
        op_kwargs={'rul_prediction': 23} # Passing the "23" from your best model
    )

    # The Flow: Train -> Then Report
    train_task >> report_task