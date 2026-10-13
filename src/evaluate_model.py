"""
Model evaluation script for AeroPredict.

Evaluates the trained LSTM model on test data and generates:
- RUL prediction metrics (MAE, RMSE, MAPE)
- Prediction vs actual scatter plot
- Training/validation loss curves
- Residual analysis
"""
import os
import sys

import matplotlib
import mlflow
import mlflow.pytorch
import numpy as np
import torch

matplotlib.use("Agg")

import joblib
import matplotlib.pyplot as plt
from sklearn.metrics import mean_absolute_error, mean_squared_error

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_preprocessing import load_and_clean_data


def evaluate_model(
    model_path: str = "models/lstm_best.pt",
    test_data_path: str = "/opt/airflow/data/test_FD001.txt",
    rul_file: str = "/opt/airflow/data/RUL_FD001.txt",
    output_dir: str = "plots",
):
    """Evaluate the LSTM model and generate diagnostic plots.

    Args:
        model_path: Path to the saved model
        test_data_path: Path to test data file
        rul_file: Path to the matching RUL_FDxxx.txt ground-truth file. Without
            it the test target is fabricated (see load_and_clean_data).
        output_dir: Directory to save plots
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1. Load Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    try:
        model = mlflow.pytorch.load_model(model_path)
    except Exception:
        model = torch.jit.load(model_path, map_location=device)

    model = model.to(device)
    model.eval()

    # 2. Load Test Data. Reuse the training scaler so evaluation matches the
    # distribution the model was fitted on.
    scaler_path = os.path.join(os.path.dirname(model_path), "scaler.joblib")
    scaler = joblib.load(scaler_path) if os.path.exists(scaler_path) else None
    if scaler is None:
        print(f"WARNING: no scaler at {scaler_path}; fitting one on the test file. "
              "Metrics will not reflect serving behaviour.")
    X_test, y_test, _ = load_and_clean_data(test_data_path, rul_file=rul_file, scaler=scaler)
    X_test = X_test.to(device)
    y_test = y_test.to(device)

    # 3. Predict
    with torch.no_grad():
        predictions = model(X_test).cpu().numpy().flatten()
        actuals = y_test.cpu().numpy().flatten()

    # 4. Calculate Metrics
    mae = mean_absolute_error(actuals, predictions)
    rmse = np.sqrt(mean_squared_error(actuals, predictions))
    mape = np.mean(np.abs((actuals - predictions) / np.maximum(actuals, 1))) * 100

    print("Test Metrics:")
    print(f"  MAE:  {mae:.2f} cycles")
    print(f"  RMSE: {rmse:.2f} cycles")
    print(f"  MAPE: {mape:.2f}%")

    # 5. Log to MLflow
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    with mlflow.start_run(run_name="evaluation"):
        mlflow.log_metrics({
            "test_mae": mae,
            "test_rmse": rmse,
            "test_mape": mape,
        })

        # 6. Generate Plots
        # Prediction vs Actual
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(actuals, predictions, alpha=0.3, s=10)
        ax.plot([0, max(actuals)], [0, max(actuals)], "r--", label="Perfect Prediction")
        ax.set_xlabel("Actual RUL (cycles)")
        ax.set_ylabel("Predicted RUL (cycles)")
        ax.set_title("RUL Prediction: Predicted vs Actual")
        ax.legend()
        plot_path = os.path.join(output_dir, "prediction_scatter.png")
        fig.savefig(plot_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(plot_path)

        # Residuals
        residuals = predictions - actuals
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(predictions, residuals, alpha=0.3, s=10)
        ax.axhline(y=0, color="r", linestyle="--")
        ax.set_xlabel("Predicted RUL (cycles)")
        ax.set_ylabel("Residuals (Pred - Actual)")
        ax.set_title("Residual Analysis")
        plot_path = os.path.join(output_dir, "residuals.png")
        fig.savefig(plot_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(plot_path)

        # RUL Distribution
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(actuals, bins=50, alpha=0.7, label="Actual", density=True)
        ax.hist(predictions, bins=50, alpha=0.7, label="Predicted", density=True)
        ax.set_xlabel("RUL (cycles)")
        ax.set_ylabel("Density")
        ax.set_title("RUL Distribution: Actual vs Predicted")
        ax.legend()
        plot_path = os.path.join(output_dir, "rul_distribution.png")
        fig.savefig(plot_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(plot_path)

    print(f"\nPlots saved to {output_dir}/")
    return {"mae": mae, "rmse": rmse, "mape": mape}


if __name__ == "__main__":
    evaluate_model()
