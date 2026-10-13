# FILE: src/data_preprocessing.py
import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import MinMaxScaler

# The 21 sensor channels the LSTM consumes. Settings and identifiers are
# excluded so training and serving agree on the feature layout.
SENSOR_COLUMNS = [f"s{i}" for i in range(1, 22)]

# Columns present in a raw C-MAPSS file that are not model inputs.
NON_FEATURE_COLUMNS = ["id", "cycle", "setting1", "setting2", "setting3"]

RAW_COLUMNS = NON_FEATURE_COLUMNS + SENSOR_COLUMNS


def load_and_clean_data(file_path, window_size=50, rul_cap=125, rul_file=None, scaler=None):
    """
    Loads data and performs sliding window transformation.

    Windows are built **per engine** so a sequence never spans two engines.

    Args:
        file_path: Path to a C-MAPSS train/test file.
        window_size: Number of cycles per input sequence.
        rul_cap: Piecewise-linear RUL ceiling. Raw RUL grows without bound for
            healthy engines (up to 361 cycles in FD001), and 39% of samples sit
            above 125. Those early cycles are indistinguishable from one another,
            so a model trained on raw RUL minimises MSE by predicting the mean
            and collapses to a near-constant output. Capping the target at 125
            is the standard C-MAPSS treatment: it keeps the informative
            degradation region intact while removing the unlearnable tail.
            Pass ``None`` to disable capping.
        rul_file: Path to the matching ``RUL_FDxxx.txt`` ground-truth file. This
            is **required** for test files. A test engine's trajectory is
            truncated before failure, so ``max_cycle - cycle`` is not its RUL --
            it always yields 0 at the final observed cycle, whereas the true
            value averages 75.5 cycles in FD001. Supplying the ground-truth file
            anchors the target at the last observed cycle and counts backwards.
        scaler: A fitted ``MinMaxScaler`` to reuse. Pass the scaler saved during
            training so evaluation sees the same feature scaling the model was
            trained on. When ``None`` a new scaler is fitted on this file.

    Returns:
        ``(X, y, scaler)`` where ``X`` is ``(n, window_size, 21)`` and ``y`` is
        ``(n, 1)``. The fitted scaler is returned so callers can persist it for
        inference.
    """
    print(f" Loading data from {file_path}...")

    df = pd.read_csv(file_path, sep=r"\s+", header=None, names=RAW_COLUMNS)

    # 1. Calculate RUL (per engine)
    max_cycles = df.groupby("id")["cycle"].max().reset_index()
    max_cycles.columns = ["id", "max_cycle"]
    df = df.merge(max_cycles, on="id", how="left")

    if rul_file is not None:
        # Test set: the true RUL is only known at the final observed cycle.
        # Anchor there and count backwards through the observed history.
        truth = pd.read_csv(rul_file, sep=r"\s+", header=None, names=["rul_at_end"])
        truth["id"] = np.arange(1, len(truth) + 1)
        df = df.merge(truth, on="id", how="left")
        if df["rul_at_end"].isna().any():
            missing = sorted(df.loc[df["rul_at_end"].isna(), "id"].unique())
            raise ValueError(f"{rul_file} has no ground-truth RUL for engine id(s) {missing}")
        df["RUL"] = df["rul_at_end"] + (df["max_cycle"] - df["cycle"])
        df = df.drop(columns=["rul_at_end"])
    else:
        df["RUL"] = df["max_cycle"] - df["cycle"]

    if rul_cap is not None:
        df["RUL"] = df["RUL"].clip(upper=rul_cap)

    # 2. Scale sensor features. Reuse the training scaler when supplied so
    # evaluation and serving match the distribution the model was fitted on.
    if scaler is None:
        scaler = MinMaxScaler()
        df[SENSOR_COLUMNS] = scaler.fit_transform(df[SENSOR_COLUMNS])
    else:
        df[SENSOR_COLUMNS] = scaler.transform(df[SENSOR_COLUMNS])

    # 3. Sliding Window built per engine to avoid boundary leakage
    X, y = [], []
    for _engine_id, engine_df in df.groupby("id", sort=False):
        engine_df = engine_df.sort_values("cycle")
        values = engine_df[SENSOR_COLUMNS].to_numpy(dtype=np.float32)
        rul = engine_df["RUL"].to_numpy(dtype=np.float32)
        if len(engine_df) < window_size:
            continue
        for i in range(len(engine_df) - window_size):
            X.append(values[i:i + window_size])
            y.append(rul[i + window_size])

    X_tensor = torch.FloatTensor(np.array(X))
    y_tensor = torch.FloatTensor(np.array(y)).view(-1, 1)

    return X_tensor, y_tensor, scaler


def preprocess_for_inference(df, scaler, window_size=50):
    """Turn raw uploaded sensor readings into a single model input window.

    This mirrors the training-time transform exactly: the same 21 sensor
    channels in the same order, scaled with the scaler fitted during training.
    Using a different normalisation here (for example z-scoring the upload)
    silently feeds the model out-of-distribution inputs.

    Args:
        df: Raw readings. May include ``id``/``cycle``/``setting*`` columns,
            which are ignored, or be bare sensor columns.
        scaler: The ``MinMaxScaler`` persisted during training.
        window_size: Number of cycles the model expects.

    Returns:
        A ``(1, window_size, 21)`` float tensor.

    Raises:
        ValueError: if no sensor columns can be identified.
    """
    numeric = df.apply(pd.to_numeric, errors="coerce")

    # Identify the sensor channels. Uploads arrive in one of three shapes:
    #   1. named sensors (s1..s21), possibly alongside id/cycle/setting columns
    #   2. a raw C-MAPSS row: id, cycle, setting1..3, s1..s21 (26 columns)
    #   3. bare sensor readings (21 columns)
    # The API reads uploads with header=None, so positional handling is needed.
    present = [c for c in SENSOR_COLUMNS if c in numeric.columns]
    if not present:
        if numeric.shape[1] == len(RAW_COLUMNS):
            numeric = numeric.iloc[:, len(NON_FEATURE_COLUMNS):]
            numeric.columns = SENSOR_COLUMNS
            present = SENSOR_COLUMNS
        elif numeric.shape[1] == len(SENSOR_COLUMNS):
            numeric.columns = SENSOR_COLUMNS
            present = SENSOR_COLUMNS
        else:
            raise ValueError(
                "Uploaded file is not valid C-MAPSS sensor data: expected 21 sensor "
                f"columns (s1..s21) or 26 raw C-MAPSS columns, but found {numeric.shape[1]}."
            )

    numeric = numeric[present].dropna(axis=1, how="all")
    if numeric.empty or numeric.isna().all().all():
        raise ValueError("Uploaded file contains no numeric sensor readings.")

    values = numeric.to_numpy(dtype=np.float32)
    values = np.nan_to_num(values, nan=0.0)

    # Pad short histories by repeating the first reading so the model always
    # receives a full window rather than a truncated sequence.
    if len(values) < window_size:
        pad = np.repeat(values[:1], window_size - len(values), axis=0)
        values = np.concatenate([pad, values], axis=0)
    else:
        values = values[-window_size:]

    scaled = scaler.transform(values)
    return torch.from_numpy(scaled.astype(np.float32)).unsqueeze(0)
