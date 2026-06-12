"""
AeroPredict DVC Pipeline - Model Training

Trains LSTM model on engineered gold layer features.
Logs metrics and model to MLflow.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import mlflow
import mlflow.pytorch
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.utils.logging_config import setup_logging

setup_logging(json_format=True, include_console=True)
logger = logging.getLogger(__name__)


class AirglowLSTM(nn.Module):
    def __init__(self, input_size=21, hidden_size=50, num_layers=2, dropout=0.2):
        super(AirglowLSTM, self).__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, 
                           batch_first=True, dropout=dropout)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = out[:, -1, :]  # Take last time step
        return self.fc(out)


def train_model(gold_path: Path, dataset_id: str, epochs: int = 10, 
                lr: float = 0.001, batch_size: int = 32):
    """
    Train LSTM model on gold layer features.
    """
    # Load data
    X = np.load(gold_path / f"{dataset_id}_X.npy")
    y = np.load(gold_path / f"{dataset_id}_y.npy")

    logger.info(f"{dataset_id}: Training on {len(X)} samples, shape {X.shape}")

    # Train/val split (80/20)
    split_idx = int(0.8 * len(X))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    # Convert to tensors
    X_train_t = torch.FloatTensor(X_train)
    y_train_t = torch.FloatTensor(y_train)
    X_val_t = torch.FloatTensor(X_val)
    y_val_t = torch.FloatTensor(y_val)

    # Create data loaders
    train_dataset = torch.utils.data.TensorDataset(X_train_t, y_train_t)
    val_dataset = torch.utils.data.TensorDataset(X_val_t, y_val_t)
    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = torch.utils.data.DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    # Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AirglowLSTM().to(device)
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)

    # MLflow setup
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    mlflow.set_experiment(f"AeroPredict_{dataset_id}")

    with mlflow.start_run() as run:
        # Log parameters
        mlflow.log_params({
            "epochs": epochs,
            "learning_rate": lr,
            "batch_size": batch_size,
            "hidden_size": 50,
            "num_layers": 2,
            "dropout": 0.2,
            "dataset": dataset_id,
        })

        # Training loop
        for epoch in range(epochs):
            model.train()
            train_loss = 0.0
            for batch_x, batch_y in train_loader:
                batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                optimizer.zero_grad()
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            # Validation
            model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for batch_x, batch_y in val_loader:
                    batch_x, batch_y = batch_x.to(device), batch_y.to(device)
                    outputs = model(batch_x)
                    val_loss += criterion(outputs, batch_y).item()

            avg_train_loss = train_loss / len(train_loader)
            avg_val_loss = val_loss / len(val_loader)

            logger.info(f"Epoch {epoch+1}/{epochs} - Train Loss: {avg_train_loss:.4f}, Val Loss: {avg_val_loss:.4f}")
            mlflow.log_metrics({
                "train_loss": avg_train_loss,
                "val_loss": avg_val_loss,
            }, step=epoch)

        # Log model
        mlflow.pytorch.log_model(model, "model")
        logger.info(f"Model logged to MLflow run: {run.info.run_id}")

    return run.info.run_id


def main():
    parser = argparse.ArgumentParser(description="Train LSTM model on gold layer features")
    parser.add_argument("--gold-path", default="data/gold", help="Gold layer path")
    parser.add_argument("--dataset", default="FD001", help="Dataset to train on")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    gold_path = Path(args.gold_path)
    run_id = train_model(gold_path, args.dataset, args.epochs, args.lr, args.batch_size)
    logger.info(f"Training complete. MLflow run: {run_id}")


if __name__ == "__main__":
    main()
