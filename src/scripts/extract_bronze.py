"""
AeroPredict DVC Pipeline - Bronze Layer: Data Extraction

Extracts raw NASA C-MAPSS data into the bronze layer for downstream processing.
Bronze layer: raw, unvalidated data as-is from source.

Usage:
    python -m src.scripts.extract_bronze --source /data/raw --output data/bronze/sensor_raw.dvc
"""

import argparse
import logging
import os
import shutil
from pathlib import Path
import pandas as pd

from src.utils.logging_config import setup_logging, log_api_request

# Setup logging
setup_logging(json_format=True, include_console=True)
logger = logging.getLogger(__name__)

# NASA C-MAPSS column names
CAMPASS_COLUMNS = [
    "id", "cycle", "setting1", "setting2", "setting3",
    "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8",
    "s9", "s10", "s11", "s12", "s13", "s14", "s15",
    "s16", "s17", "s18", "s19", "s20", "s21"
]

VALID_DATASETS = ["FD001", "FD002", "FD003", "FD004"]


def extract_dataset(source_dir: str, output_dir: str, dataset_name: str = None):
    """
    Extract a NASA C-MAPSS dataset to bronze layer.

    Args:
        source_dir: Directory containing raw C-MAPSS text files
        output_dir: Where to write the bronze layer data
        dataset_name: Specific dataset (FD001-FD004), None for all
    """
    source_path = Path(source_dir)
    output_path = Path(output_path)

    if not source_path.exists():
        logger.error(f"Source directory not found: {source_path}")
        raise FileNotFoundError(f"Source directory not found: {source_path}")

    output_path.mkdir(parents=True, exist_ok=True)

    datasets_found = []
    for txt_file in sorted(source_path.glob("train_*.txt")):
        dataset_id = txt_file.stem.replace("train_", "")
        if dataset_name and dataset_id != dataset_name:
            continue

        logger.info(f"Extracting dataset {dataset_id}...")

        # Read raw C-MAPSS data (space-separated, no header)
        df = pd.read_csv(txt_file, sep=r"\s+", header=None, names=CAMPASS_COLUMNS)

        # Save to bronze layer (preserve raw data)
        bronze_file = output_path / f"{dataset_id}_train.csv"
        df.to_csv(bronze_file, index=False)
        datasets_found.append(dataset_id)
        logger.info(f"Extracted {len(df)} rows to {bronze_file}")

        # Also extract test data and ground truth if available
        test_file = source_path / f"test_{dataset_id}.txt"
        if test_file.exists():
            test_df = pd.read_csv(test_file, sep=r"\s+", header=None, names=CAMPASS_COLUMNS)
            test_df.to_csv(output_path / f"{dataset_id}_test.csv", index=False)

        rul_file = source_path / f"RUL_{dataset_id}.txt"
        if rul_file.exists():
            rul_df = pd.read_csv(rul_file, sep=r"\s+", header=None, names=["RUL"])
            rul_df.to_csv(output_path / f"{dataset_id}_rul.csv", index=False)

    # Save metadata
    metadata = {
        "datasets_extracted": datasets_found,
        "source_path": str(source_path),
        "extraction_timestamp": pd.Timestamp.now().isoformat(),
        "columns": CAMPASS_COLUMNS,
    }
    pd.DataFrame([metadata]).to_json(output_path / "_metadata.json", indent=2)

    return datasets_found


def main():
    parser = argparse.ArgumentParser(description="Extract data to bronze layer")
    parser.add_argument("--source", default="/data/raw", help="Source directory")
    parser.add_argument("--output", default="data/bronze", help="Output directory")
    parser.add_argument("--dataset", choices=VALID_DATASETS, help="Specific dataset to extract")
    args = parser.parse_args()

    datasets = extract_dataset(args.source, args.output, args.dataset)
    logger.info(f"Extraction complete. Datasets: {datasets}")


if __name__ == "__main__":
    main()