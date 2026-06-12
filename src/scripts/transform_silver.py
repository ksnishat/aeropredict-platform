"""
AeroPredict DVC Pipeline - Silver Layer: Data Cleaning

Cleans and validates raw C-MAPSS data from bronze layer.
Silver layer: cleaned, deduplicated, validated data.

Usage:
    python -m src.scripts.transform_silver --input data/bronze --output data/silver
"""

import argparse
import json
import logging
from pathlib import Path
import pandas as pd
import numpy as np

from src.utils.logging_config import setup_logging

setup_logging(json_format=True, include_console=True)
logger = logging.getLogger(__name__)


def clean_dataset(bronze_path: Path, output_path: Path, dataset_id: str):
    """
    Clean a single C-MAPSS dataset.
    - Remove duplicate rows
    - Validate sensor readings
    - Handle missing values
    - Calculate data quality metrics
    """
    input_file = bronze_path / f"{dataset_id}_train.csv"
    if not input_file.exists():
        logger.warning(f"Skipping {dataset_id}: no train file found")
        return None

    df = pd.read_csv(input_file)
    initial_rows = len(df)

    # Drop duplicates
    df = df.drop_duplicates()
    after_dedup = len(df)
    dropped_dupes = initial_rows - after_dedup

    # Validate sensor columns (s1-s21 should be non-negative)
    sensor_cols = [f"s{i}" for i in range(1, 22)]
    validation_errors = []
    for col in sensor_cols:
        if col in df.columns:
            negative_count = (df[col] < 0).sum()
            if negative_count > 0:
                logger.warning(f"{dataset_id}: {negative_count} negative values in {col}")
                validation_errors.append(f"{col}: {negative_count} negative values")

    # Check for NaN values
    nan_count = df.isnull().sum().sum()
    if nan_count > 0:
        logger.warning(f"{dataset_id}: {nan_count} NaN values found, filling...")
        df = df.fillna(method="ffill").fillna(method="bfill")

    # Validate cycle column
    invalid_cycles = (df["cycle"] <= 0).sum()
    if invalid_cycles > 0:
        logger.warning(f"{dataset_id}: {invalid_cycles} invalid cycle values")
        df = df[df["cycle"] > 0]

    # Save cleaned data
    output_file = output_path / f"{dataset_id}_train.csv"
    df.to_csv(output_file, index=False)

    # Quality metrics
    quality = {
        "dataset": dataset_id,
        "initial_rows": initial_rows,
        "final_rows": len(df),
        "rows_dropped": initial_rows - len(df),
        "duplicates_removed": dropped_dupes,
        "nan_values": int(nan_count),
        "validation_errors": validation_errors,
        "cleaning_timestamp": pd.Timestamp.now().isoformat(),
    }

    return quality


def main():
    parser = argparse.ArgumentParser(description="Transform bronze data to silver layer")
    parser.add_argument("--input", default="data/bronze", help="Input directory")
    parser.add_argument("--output", default="data/silver", help="Output directory")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    qualities = []
    for csv_file in sorted(input_path.glob("*_train.csv")):
        dataset_id = csv_file.stem.replace("_train", "")
        if dataset_id.startswith("_"):
            continue
        logger.info(f"Cleaning dataset: {dataset_id}")
        quality = clean_dataset(input_path, output_path, dataset_id)
        if quality:
            qualities.append(quality)

    # Save quality report
    report_file = output_path / "_quality_report.json"
    with open(report_file, "w") as f:
        json.dump(qualities, f, indent=2)

    logger.info(f"Cleaning complete. {len(qualities)} datasets processed.")


if __name__ == "__main__":
    main()
