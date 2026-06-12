# FILE: src/data_preprocessing.py
import pandas as pd
import numpy as np
import torch
from sklearn.preprocessing import MinMaxScaler

def load_and_clean_data(file_path):
    """
    Loads data and performs sliding window transformation.
    """
    print(f" Loading data from {file_path}...")
    
    # 1. Load Data
    columns = ['id', 'cycle', 'setting1', 'setting2', 'setting3', 's1', 's2', 's3',
               's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11', 's12', 's13', 's14',
               's15', 's16', 's17', 's18', 's19', 's20', 's21']
    df = pd.read_csv(file_path, sep='\s+', header=None, names=columns)
    
    # 2. Calculate RUL
    max_cycles = df.groupby('id')['cycle'].max().reset_index()
    max_cycles.columns = ['id', 'max_cycle']
    df = df.merge(max_cycles, on='id', how='left')
    df['RUL'] = df['max_cycle'] - df['cycle']
    
    # 3. Normalize
    drop_cols = ['id', 'cycle', 'setting1', 'setting2', 'setting3', 'max_cycle']
    data = df.drop(columns=drop_cols)
    scaler = MinMaxScaler()
    scaled_data = scaler.fit_transform(data.drop(columns=['RUL']))
    
    # 4. Sliding Window (Window=50)
    window_size = 50
    X, y = [], []
    for i in range(len(scaled_data) - window_size):
        X.append(scaled_data[i : i + window_size])
        y.append(data['RUL'].iloc[i + window_size])
        
    X_tensor = torch.FloatTensor(np.array(X))
    y_tensor = torch.FloatTensor(np.array(y)).view(-1, 1)
    
    return X_tensor, y_tensor