# FILE: src/train_model.py
import torch
import torch.nn as nn
import torch.optim as optim
import mlflow
import mlflow.pytorch
import os
import sys

# Add the parent directory to path so we can import 'src' if needed
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data_preprocessing import load_and_clean_data

# Define LSTM (Must match notebook)
class AirglowLSTM(nn.Module):
    def __init__(self, input_size=21, hidden_size=50, num_layers=2):
        super(AirglowLSTM, self).__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, 1)
        
    def forward(self, x):
        out, _ = self.lstm(x)
        out = out[:, -1, :] 
        return self.fc(out)

def train():
    # 1. Config MLOps
    mlflow.set_tracking_uri("http://mlflow:5000")
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = "http://minio:9000"
    os.environ["AWS_ACCESS_KEY_ID"] = "minio"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "minio123"
    
    # 2. Load Data (Hardcoded path for Docker)
    print(" Starting Automated Training...")
    # Note: Airflow mounts data to /opt/airflow/data
    X_train, y_train = load_and_clean_data("/opt/airflow/data/train_FD001.txt")
    
    # 3. GPU Setup
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AirglowLSTM().to(device)
    X_train = X_train.to(device)
    y_train = y_train.to(device)
    
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    
    # 4. Train & Log
    mlflow.set_experiment("Airflow_Automated_Training")
    with mlflow.start_run():
        epochs = 10 # Short run for testing
        for epoch in range(epochs):
            model.train()
            optimizer.zero_grad()
            outputs = model(X_train)
            loss = criterion(outputs, y_train)
            loss.backward()
            optimizer.step()
            print(f"Epoch {epoch+1}/{epochs} Loss: {loss.item()}")
            
        mlflow.log_metric("final_loss", loss.item())
        mlflow.pytorch.log_model(model, "model")
        print(" Model saved to MLflow!")

if __name__ == "__main__":
    train()