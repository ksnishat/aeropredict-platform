from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import os
from pyspark.sql import SparkSession

# --- CONFIGURATION ---
# We use the mounted data path defined in docker-compose
DATA_ROOT = "/opt/airflow/data"
RAW_DATA = f"{DATA_ROOT}/raw/sample_FD001.txt"
PROCESSED_DATA = f"{DATA_ROOT}/processed/bronze"

def run_spark_ingestion():
    print(" Starting Spark Session...")
    
    # 1. Initialize Spark (Uses the Java engine we installed)
    spark = SparkSession.builder \
        .appName("AeroPredict_Ingest") \
        .config("spark.driver.memory", "1g") \
        .getOrCreate()
    
    print(f" Reading data from: {RAW_DATA}")
    
    # 2. Define Schema (CMAPSS Data has no headers, so we name them manually)
    columns = ["unit_nr", "time_cycles", "setting_1", "setting_2", "setting_3"] + \
              [f"s_{i}" for i in range(1, 22)]
    
    # 3. Read TXT file
    # inferSchema=true lets Spark figure out which columns are numbers
    df = spark.read.option("delimiter", " ") \
        .option("header", "false") \
        .option("inferSchema", "true") \
        .csv(RAW_DATA)
    
    # 4. Clean up (Drop extra empty columns caused by trailing spaces)
    df = df.select(df.columns[:26])
    
    # 5. Rename Columns
    for old_col, new_col in zip(df.columns, columns):
        df = df.withColumnRenamed(old_col, new_col)

    print(" Data Preview:")
    df.show(5)
    
    # 6. Write to Parquet (The "Bronze" Layer)
    # mode("overwrite") ensures we don't crash if we run it twice
    print(f" Saving to: {PROCESSED_DATA}")
    df.write.mode("overwrite").parquet(PROCESSED_DATA)
    
    print(" Ingestion Complete!")
    spark.stop()

# --- DAG DEFINITION ---
default_args = {
    'owner': 'nishat',
    'start_date': datetime(2024, 1, 1),
    'retries': 0,
}

with DAG('01_ingest_data',
         default_args=default_args,
         schedule_interval=None,  # Manual trigger only
         catchup=False) as dag:

    ingest_task = PythonOperator(
        task_id='ingest_nasa_data',
        python_callable=run_spark_ingestion
    )
