# FILE: src/train_model.py
"""
AeroPredict LSTM Training Module.

Trains a PyTorch LSTM model for Remaining Useful Life (RUL) prediction
on NASA C-MAPSS sensor data. Includes:
- Train/validation split (80/20)
- Early stopping with patience
- Asymmetric safety-first loss (penalizes late predictions more)
- MLflow experiment tracking with full metrics logging
- Model checkpointing (best + last)
"""
import os
import sys

import joblib
import mlflow
import mlflow.pytorch
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from packaging.version import Version

# Add the parent directory to path so we can import 'src' if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_preprocessing import load_and_clean_data


# Define LSTM (Must match notebook)
class AirglowLSTM(nn.Module):
    """LSTM model for RUL prediction.

    Architecture:
        - LSTM with configurable hidden size and layers
        - Dropout between LSTM layers (0.2)
        - Linear output layer for regression
    """

    def __init__(self, input_size=21, hidden_size=50, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size, hidden_size, num_layers,
            batch_first=True, dropout=0.2
        )
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = out[:, -1, :]
        return self.fc(out)


class AsymmetricLoss(nn.Module):
    """Safety-first loss: penalizes late predictions (underestimation) more.

    In predictive maintenance, predicting RUL too high (late prediction)
    is more dangerous than predicting too low (early prediction).
    This loss applies a higher weight to underestimation errors.
    """

    def __init__(self, late_penalty: float = 5.0):
        super().__init__()
        self.late_penalty = late_penalty
        self.mse = nn.MSELoss(reduction="none")

    def forward(self, pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        errors = self.mse(pred, target)
        # Underestimation (pred < target) gets higher penalty
        mask = pred < target
        weighted = errors * torch.where(mask, self.late_penalty, 1.0)
        return weighted.mean()


def train(
    epochs: int = 50,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    hidden_size: int = 50,
    num_layers: int = 2,
    patience: int = 10,
    val_split: float = 0.2,
    data_path: str = "/opt/airflow/data/train_FD001.txt",
    rul_cap: int = 125,
    model_dir: str = "models",
):
    """Train the LSTM model with validation, early stopping, and MLflow tracking.

    Args:
        epochs: Maximum number of training epochs
        batch_size: Training batch size
        learning_rate: Adam optimizer learning rate
        hidden_size: LSTM hidden state dimension
        num_layers: Number of LSTM layers
        patience: Early stopping patience (epochs without improvement)
        val_split: Fraction of data for validation
        data_path: Path to the C-MAPSS training data file
        rul_cap: Piecewise-linear RUL ceiling (see load_and_clean_data)
        model_dir: Directory for the exported TorchScript model and scaler
    """
    # 1. Config MLOps (overridable for local runs outside docker-compose)
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", os.getenv("MLFLOW_S3_ENDPOINT_URL", "http://minio:9000"))
    os.environ.setdefault("AWS_ACCESS_KEY_ID", os.getenv("AWS_ACCESS_KEY_ID", "minio"))
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.getenv("AWS_SECRET_ACCESS_KEY", "minio123"))

    # 2. Load Data
    print("Starting Automated Training...")
    X, y, scaler = load_and_clean_data(data_path, rul_cap=rul_cap)

    # 3. Train/Validation Split (time-series aware: no shuffle)
    n_samples = len(X)
    n_val = int(n_samples * val_split)
    n_train = n_samples - n_val

    X_train, X_val = X[:n_train], X[n_train:]
    y_train, y_val = y[:n_train], y[n_train:]

    print(f"Data split: {n_train} train, {n_val} validation samples")

    # 4. GPU Setup
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AirglowLSTM(
        input_size=X_train.shape[2],
        hidden_size=hidden_size,
        num_layers=num_layers,
    ).to(device)

    X_train = X_train.to(device)
    y_train = y_train.to(device)
    X_val = X_val.to(device)
    y_val = y_val.to(device)

    # 5. Loss & Optimizer
    criterion = AsymmetricLoss(late_penalty=5.0)
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)

    # 6. Training Loop with Early Stopping
    mlflow.set_experiment("Airflow_Automated_Training")
    with mlflow.start_run():
        # Log hyperparameters
        mlflow.log_params({
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "hidden_size": hidden_size,
            "num_layers": num_layers,
            "patience": patience,
            "val_split": val_split,
            "late_penalty": 5.0,
            "device": str(device),
        })

        best_val_loss = float("inf")
        patience_counter = 0
        best_model_state = None

        for epoch in range(epochs):
            # --- Training ---
            model.train()
            train_losses = []

            # Mini-batch training
            for i in range(0, len(X_train), batch_size):
                batch_X = X_train[i:i + batch_size]
                batch_y = y_train[i:i + batch_size]

                optimizer.zero_grad()
                outputs = model(batch_X)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                train_losses.append(loss.item())

            avg_train_loss = np.mean(train_losses)

            # --- Validation ---
            model.eval()
            with torch.no_grad():
                val_outputs = model(X_val)
                val_loss = criterion(val_outputs, y_val).item()

            # Log metrics
            mlflow.log_metrics({
                "train_loss": avg_train_loss,
                "val_loss": val_loss,
                "epoch": epoch + 1,
            }, step=epoch)

            print(f"Epoch {epoch+1}/{epochs} | Train Loss: {avg_train_loss:.4f} | Val Loss: {val_loss:.4f}")

            # --- Early Stopping ---
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                best_model_state = model.state_dict().copy()
                mlflow.log_metric("best_val_loss", best_val_loss, step=epoch)
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    print(f"Early stopping at epoch {epoch+1} (no improvement for {patience} epochs)")
                    break

        # 7. Save Best Model
        if best_model_state is not None:
            model.load_state_dict(best_model_state)

        # Log final metrics
        mlflow.log_metric("final_train_loss", avg_train_loss)
        mlflow.log_metric("final_val_loss", val_loss)
        mlflow.log_metric("best_val_loss", best_val_loss)

        # Log model. MLflow infers the signature from input_example and accepts
        # numpy arrays but not torch tensors, so convert first.
        input_example = X[:1].detach().cpu().numpy()
        log_kwargs = {"input_example": input_example}
        # serialization_format only exists in MLflow 3.x. The Airflow container
        # pins MLflow 2.x, so pass it only when the installed version supports
        # it rather than failing the whole training run.
        if Version(mlflow.__version__) >= Version("3.0.0"):
            log_kwargs["serialization_format"] = "pickle"
        # Best-effort: the tracking server's artifact root may be a path this
        # process cannot write to (for example a host directory when training
        # runs inside a container). Params and metrics still reach the backend
        # store over HTTP, and the model is exported to model_dir below, so a
        # failed artifact upload must not fail an otherwise successful run.
        try:
            mlflow.pytorch.log_model(model, "model", **log_kwargs)
            print(f"Model saved to MLflow! Best val loss: {best_val_loss:.4f}")
        except Exception as e:
            print(f"WARNING: could not log model to MLflow: {e}")

        # 8. Export for serving. The API loads a TorchScript module and needs
        # the exact scaler used here, otherwise it feeds the model
        # out-of-distribution inputs and predictions are meaningless.
        os.makedirs(model_dir, exist_ok=True)
        scaler_path = os.path.join(model_dir, "scaler.joblib")
        joblib.dump(scaler, scaler_path)

        model_cpu = model.to("cpu").eval()
        # Use script, not trace: tracing bakes the initial hidden state in as a
        # CPU constant, so the exported module fails with "Input and hidden
        # tensors are not at the same device" the moment it is moved to CUDA.
        scripted = torch.jit.script(model_cpu)
        torchscript_path = os.path.join(model_dir, "lstm_best.pt")
        scripted.save(torchscript_path)
        print(f"Exported TorchScript model to {torchscript_path} and scaler to {scaler_path}")

        # Artifact logging is best-effort. The tracking server may use an
        # artifact root this process cannot write to (for example a host path
        # when training runs inside a container), and losing the run's
        # artifacts must not fail an otherwise successful training job.
        for artifact in (scaler_path, torchscript_path):
            try:
                mlflow.log_artifact(artifact)
            except Exception as e:
                print(f"WARNING: could not log {artifact} to MLflow: {e}")

        return model


if __name__ == "__main__":
    train()
