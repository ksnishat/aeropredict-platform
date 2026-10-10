# FILE: src/data_preprocessing.py
import pandas as pd
import numpy as np
import torch
from sklearn.preprocessing import MinMaxScaler

def load_and_clean_data(file_path):
    """
    Loads data and performs sliding window transformation.

    Windows are built **per engine** so a sequence never spans two engines,
    and features are scaled per engine to keep the model stationary-friendly.
    """
    print(f" Loading data from {file_path}...")

    # 1. Load Data
    columns = ['id', 'cycle', 'setting1', 'setting2', 'setting3', 's1', 's2', 's3',
               's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11', 's12', 's13', 's14',
               's15', 's16', 's17', 's18', 's19', 's20', 's21']
    df = pd.read_csv(file_path, sep=r'\s+', header=None, names=columns)

    # 2. Calculate RUL (per engine)
    max_cycles = df.groupby('id')['cycle'].max().reset_index()
    max_cycles.columns = ['id', 'max_cycle']
    df = df.merge(max_cycles, on='id', how='left')
    df['RUL'] = df['max_cycle'] - df['cycle']

    # 3. Scale sensor features globally (fit on train distribution).
    # Settings are excluded, keeping the 21 sensor features expected by the LSTM.
    drop_cols = ['id', 'cycle', 'setting1', 'setting2', 'setting3', 'max_cycle', 'RUL']
    feature_cols = [c for c in df.columns if c not in drop_cols]
    scaler = MinMaxScaler()
    df[feature_cols] = scaler.fit_transform(df[feature_cols])

    # 4. Sliding Window (Window=50) built per engine to avoid boundary leakage
    window_size = 50
    X, y = [], []
    for _engine_id, engine_df in df.groupby('id', sort=False):
        engine_df = engine_df.sort_values('cycle')
        values = engine_df[feature_cols].to_numpy(dtype=np.float32)
        rul = engine_df['RUL'].to_numpy(dtype=np.float32)
        if len(engine_df) < window_size:
            continue
        for i in range(len(engine_df) - window_size):
            X.append(values[i:i + window_size])
            y.append(rul[i + window_size])

    X_tensor = torch.FloatTensor(np.array(X))
    y_tensor = torch.FloatTensor(np.array(y)).view(-1, 1)

    return X_tensor, y_tensor