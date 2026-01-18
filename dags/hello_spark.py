from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import sys

def check_spark():
    import pyspark
    import mlflow
    print(f"✅ SUCCESS! Spark version {pyspark.__version__} is ready.")
    print(f"✅ SUCCESS! MLflow version {mlflow.__version__} is ready.")

with DAG(
    dag_id='01_spark_check',
    start_date=datetime(2023, 1, 1),
    schedule_interval=None,
    catchup=False
) as dag:

    task_1 = PythonOperator(
        task_id='verify_installation',
        python_callable=check_spark
    )
