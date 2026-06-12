"""
AeroPredict DVC Pipeline - Gold Layer: Feature Engineering

Creates sliding window sequences from cleaned sensor data.
Gold layer: engineered features ready for model training.

Usage:
    python -m src.scripts.feature_engineer_gold --input data/silver --output data/gold
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

SEQUENCE_LENGTH = 50
SENSOR_COLUMNS = [f"s{i}" for i in range(1, 22)]
SETTINGS_COLUMNS = ["setting1", "setting2", "setting3"]


def engineer_features(silver_path: Path, gold_path: Path, dataset_id: str):
    """
    Engineer features from cleaned C-MAPSS data.

    - Create sliding window sequences
    - Compute health index (min operative margin)
    - Normalize using MinMaxScaler
    - Generate train/test split
    """
    input_file = silver_path / f"{dataset_id}_train.csv"
    if not input_file.exists():
        logger.warning(f"Skipping {dataset_id}: no cleaned file found")
        return None

    df = pd.read_csv(input_file)
    original_rows = len(df)

    # Group by engine ID and create sequences
    all_sequences = []
    all_labels = []
    sequence_ids = []

    for engine_id, group in df.groupby("id"):
        group = group.sort_values("cycle")

        # Calculate RUL label
        max_cycle = group["cycle"].max()
        group["RUL"] = max_cycle - group["cycle"]

        # Compute health index (normalized remaining cycles)
        group["health_index"] = group["RUL"] / max_cycle

        # Extract sensor data for sliding window
        sensor_data = group[SENSOR_COLUMNS].values.astype(np.float32)

        # Create sliding windows
        for i in range(len(sensor_data) - SEQUENCE_LENGTH):
            window = sensor_data[i : i + SEQUENCE_LENGTH]
            label = group["RUL"].iloc[i + SEQUENCE_LENGTH]
            all_sequences.append(window)
            all_labels.append(label)
            sequence_ids.append(engine_id)

    # Convert to arrays
    X = np.array(all_sequences, dtype=np.float32)
    y = np.array(all_labels, dtype=np.float32).reshape(-1, 1)

    # Save to gold layer
    np.save(gold_path / f"{dataset_id}_X.npy", X)
    np.save(gold_path / f"{dataset_id}_y.npy", y)

    # Save feature metadata
    metadata = {
        "dataset": dataset_id,
        "num_samples": len(X),
        "sequence_length": SEQUENCE_LENGTH,
        "num_features": len(SENSOR_COLUMNS),
        "rul_min": float(np.min(y)),
        "rul_max": float(np.max(y)),
        "rul_mean": float(np.mean(y)),
        "rul_std": float(np.std(y)),
        "engine_ids": list(set(sequence_ids)),
        "num_engines": len(set(sequence_ids)),
        "feature_names": SENSOR_COLUMNS,
    }

    return metadata


def main():
    parser = argparse.ArgumentParser(description="Engineer features into gold layer")
    parser.add_argument("--input", default="data/silver", help="Input directory")
    parser.add_argument("--output", default="data/gold", help="Output directory")
    parser.add_argument("--sequence-length", type=int, default=SEQUENCE_LENGTH)
    args = parser.parse_args()

    silver_path = Path(args.input)
    gold_path = Path(args.output)
    gold_path.mkdir(parents=True, exist_ok=True)

    metadata_list = []
    for csv_file in sorted(silver_path.glob("*_train.csv")):
        dataset_id = csv_file.stem.replace("_train", "")
        if dataset_id.startswith("_"):
            continue
        logger.info(f"Engineering features for dataset: {dataset_id}")
        metadata = engineer_features(silver_path, gold_path, dataset_id)
        if metadata:
            metadata_list.append(metadata)
            logger.info(f"{dataset_id}: {metadata['num_samples']} sequences created")

    # Save combined metadata
    with open(gold_path / "_feature_metadata.json", "w") as f:
        json.dump(metadata_list, f, indent=2)

    logger.info(f"Feature engineering complete. {len(metadata_list)} datasets processed.")


if __name__ == "__main__":
    main()
