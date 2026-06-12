"""
AeroPredict LSTM Training with PyTorch Lightning.

Refactored from src/train_model.py to use PyTorch Lightning for:
- Cleaner training loop (no manual epoch/batch loops)
- Automatic checkpointing (best model saved automatically)
- Built-in logging (TensorBoard, MLflow via logger)
- Easy GPU/CPU switching
- Gradient clipping, early stopping, learning rate scheduling

Usage:
    python src/scripts/train_lightning.py --epochs 50 --batch_size 32
"""
import os
import sys
import argparse
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import pytorch_lightning as pl
from pytorch_lightning.callbacks import ModelCheckpoint, EarlyStopping
from pytorch_lightning.loggers import MLFlowLogger

# Add src to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_preprocessing import load_and_clean_data


class AirglowLSTM(pl.LightningModule):
    """LSTM model for RUL prediction using PyTorch Lightning.

    Architecture:
        - LSTM with configurable hidden size and layers
        - Dropout between LSTM layers (0.2)
        - Linear output layer for regression
        - Asymmetric safety-first loss (penalizes late predictions more)
    """

    def __init__(
        self,
        input_size: int = 21,
        hidden_size: int = 50,
        num_layers: int = 2,
        learning_rate: float = 0.001,
        late_penalty: float = 5.0,
    ):
        super().__init__()
        self.save_hyperparameters()

        self.lstm = nn.LSTM(
            input_size, hidden_size, num_layers,
            batch_first=True, dropout=0.2 if num_layers > 1 else 0.0
        )
        self.fc = nn.Linear(hidden_size, 1)
        self.late_penalty = late_penalty

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        out = out[:, -1, :]
        return self.fc(out)

    def asymmetric_loss(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        """Safety-first loss: penalizes underestimation (late prediction) more."""
        mse = nn.functional.mse_loss(pred, target, reduction="none")
        mask = pred < target
        weighted = mse * torch.where(mask, self.late_penalty, 1.0)
        return weighted.mean()

    def training_step(self, batch, batch_idx):
        x, y = batch
        pred = self(x)
        loss = self.asymmetric_loss(pred, y)
        self.log("train_loss", loss, prog_bar=True, on_step=False, on_epoch=True)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        pred = self(x)
        loss = self.asymmetric_loss(pred, y)
        self.log("val_loss", loss, prog_bar=True, on_step=False, on_epoch=True)

        # Log additional metrics
        mae = nn.functional.l1_loss(pred, y)
        self.log("val_mae", mae, prog_bar=True, on_step=False, on_epoch=True)

        return loss

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.hparams.learning_rate)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5
        )
        return {
            "optimizer": optimizer,
            "lr_scheduler": {
                "scheduler": scheduler,
                "monitor": "val_loss",
            },
        }


def train_lightning(
    data_path: str = "/opt/airflow/data/train_FD001.txt",
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    hidden_size: int = 50,
    num_layers: int = 2,
    patience: int = 10,
    val_split: float = 0.2,
    mlflow_tracking_uri: str = "http://mlflow:5000",
):
    """Train the LSTM model using PyTorch Lightning.

    Args:
        data_path: Path to the C-MAPSS training data file
        epochs: Maximum number of training epochs
        batch_size: Training batch size
        learning_rate: Adam optimizer learning rate
        hidden_size: LSTM hidden state dimension
        num_layers: Number of LSTM layers
        patience: Early stopping patience
        val_split: Fraction of data for validation
        mlflow_tracking_uri: MLflow tracking server URI
    """
    # 1. Load Data
    print(f"Loading data from {data_path}...")
    X, y = load_and_clean_data(data_path)

    # 2. Train/Validation Split (time-series aware)
    n_samples = len(X)
    n_val = int(n_samples * val_split)
    n_train = n_samples - n_val

    X_train, X_val = X[:n_train], X[n_train:]
    y_train, y_val = y[:n_train], y[n_train:]

    print(f"Data split: {n_train} train, {n_val} validation samples")

    # 3. Create DataLoaders
    train_dataset = TensorDataset(X_train, y_train)
    val_dataset = TensorDataset(X_val, y_val)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    # 4. Setup MLflow Logger
    mlflow_logger = MLFlowLogger(
        experiment_name="AeroPredict_Lightning",
        tracking_uri=mlflow_tracking_uri,
        run_name=f"lstm_h{hidden_size}_l{num_layers}_lr{learning_rate}",
    )

    # 5. Callbacks
    checkpoint_callback = ModelCheckpoint(
        dirpath="models/lightning",
        filename="aeropredict-lstm-best",
        save_top_one=True,
        save_last=True,
        monitor="val_loss",
        mode="min",
    )

    early_stopping = EarlyStopping(
        monitor="val_loss",
        patience=patience,
        mode="min",
        verbose=True,
    )

    # 6. Trainer
    trainer = pl.Trainer(
        max_epochs=epochs,
        accelerator="auto",
        devices="auto",
        logger=mlflow_logger,
        callbacks=[checkpoint_callback, early_stopping],
        gradient_clip_val=1.0,
        log_every_n_steps=10,
    )

    # 7. Model
    model = AirglowLSTM(
        input_size=X_train.shape[2],
        hidden_size=hidden_size,
        num_layers=num_layers,
        learning_rate=learning_rate,
    )

    # 8. Train
    print(f"Training with PyTorch Lightning for {epochs} epochs...")
    trainer.fit(model, train_loader, val_loader)

    # 9. Log final metrics
    best_val_loss = checkpoint_callback.best_model_path
    print(f"Best model saved to: {best_val_loss}")

    # Log model to MLflow
    mlflow_logger.experiment.log_artifact(
        mlflow_logger.run_id,
        best_val_loss,
        artifact_path="model",
    )

    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train AeroPredict LSTM with PyTorch Lightning")
    parser.add_argument("--data_path", type=str, default="/opt/airflow/data/train_FD001.txt")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--learning_rate", type=float, default=0.001)
    parser.add_argument("--hidden_size", type=int, default=50)
    parser.add_argument("--num_layers", type=int, default=2)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--val_split", type=float, default=0.2)
    parser.add_argument("--mlflow_uri", type=str, default="http://mlflow:5000")

    args = parser.parse_args()

    train_lightning(
        data_path=args.data_path,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
        patience=args.patience,
        val_split=args.val_split,
        mlflow_tracking_uri=args.mlflow_uri,
    )