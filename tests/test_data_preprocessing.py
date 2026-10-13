"""Tests for the RUL target construction and inference preprocessing.

These lock in three bugs that made the model useless:

1. The training target was uncapped. 39% of FD001 samples have RUL > 125 and
   are indistinguishable from one another, so the model minimised MSE by
   predicting the mean and collapsed to a near-constant output.
2. The test target was fabricated. A test engine's trajectory is truncated
   before failure, so ``max_cycle - cycle`` always yields 0 at the final
   observed cycle, whereas the true value averages 75.5 cycles.
3. Serving z-scored the upload while training MinMax-scaled it, so the model
   received out-of-distribution inputs.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from data_preprocessing import (  # noqa: E402
    RAW_COLUMNS,
    SENSOR_COLUMNS,
    load_and_clean_data,
    preprocess_for_inference,
)

DATA_DIR = REPO_ROOT / "Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main" / "CMAPSSData"
TRAIN_FILE = DATA_DIR / "train_FD001.txt"
TEST_FILE = DATA_DIR / "test_FD001.txt"
RUL_FILE = DATA_DIR / "RUL_FD001.txt"

requires_data = pytest.mark.skipif(
    not TRAIN_FILE.exists(), reason="C-MAPSS data not present"
)


def _write_engine(path: Path, n_cycles: int, engine_id: int = 1, seed: int = 0):
    """Write a minimal C-MAPSS file with one engine of n_cycles."""
    rng = np.random.default_rng(seed)
    rows = []
    for cycle in range(1, n_cycles + 1):
        rows.append([engine_id, cycle, 0.0, 0.0, 100.0] + list(rng.normal(size=21)))
    pd.DataFrame(rows).to_csv(path, sep=" ", header=False, index=False)


class TestRulCap:
    def test_target_never_exceeds_the_cap(self, tmp_path):
        """Regression: uncapped RUL let the model collapse to the mean."""
        f = tmp_path / "train.txt"
        _write_engine(f, 300)

        _, y, _ = load_and_clean_data(f, rul_cap=125)

        assert float(y.max()) == 125.0

    def test_cap_is_configurable(self, tmp_path):
        f = tmp_path / "train.txt"
        _write_engine(f, 300)

        _, y, _ = load_and_clean_data(f, rul_cap=80)

        assert float(y.max()) == 80.0

    def test_cap_can_be_disabled(self, tmp_path):
        f = tmp_path / "train.txt"
        _write_engine(f, 300)

        _, y, _ = load_and_clean_data(f, rul_cap=None)

        assert float(y.max()) > 125.0

    def test_capping_reduces_target_variance(self, tmp_path):
        """The whole point of the cap: a tighter, learnable target."""
        f = tmp_path / "train.txt"
        _write_engine(f, 300)

        _, y_raw, _ = load_and_clean_data(f, rul_cap=None)
        _, y_capped, _ = load_and_clean_data(f, rul_cap=125)

        assert float(y_capped.std()) < float(y_raw.std())


class TestTestSetGroundTruth:
    def test_rul_file_anchors_the_target(self, tmp_path):
        """Regression: without the ground-truth file the final cycle's RUL was 0."""
        f = tmp_path / "test.txt"
        _write_engine(f, 60)
        rul = tmp_path / "RUL.txt"
        rul.write_text("42\n")

        X, y, _ = load_and_clean_data(f, rul_file=rul)

        # The last window ends at the final observed cycle, whose true RUL is 42.
        assert float(y[-1]) == 42.0

    def test_without_rul_file_the_target_is_wrong(self, tmp_path):
        """Documents the bug: the fabricated target is 0 at the last cycle."""
        f = tmp_path / "test.txt"
        _write_engine(f, 60)

        _, y, _ = load_and_clean_data(f)

        assert float(y[-1]) == 0.0

    def test_missing_engine_in_rul_file_raises(self, tmp_path):
        f = tmp_path / "test.txt"
        _write_engine(f, 60, engine_id=1)
        _write_engine(f, 60, engine_id=2, seed=1)
        rul = tmp_path / "RUL.txt"
        rul.write_text("42\n")  # only one row for two engines

        with pytest.raises(ValueError, match="no ground-truth RUL"):
            load_and_clean_data(f, rul_file=rul)


class TestScalerReuse:
    def test_scaler_is_returned(self, tmp_path):
        f = tmp_path / "train.txt"
        _write_engine(f, 100)

        _, _, scaler = load_and_clean_data(f)

        assert scaler is not None
        assert hasattr(scaler, "transform")

    def test_supplied_scaler_is_reused_not_refitted(self, tmp_path):
        """Evaluation must see the training distribution, not its own."""
        train = tmp_path / "train.txt"
        _write_engine(train, 100, seed=0)
        _, _, scaler = load_and_clean_data(train)

        other = tmp_path / "other.txt"
        _write_engine(other, 100, seed=99)
        X_a, _, _ = load_and_clean_data(other, scaler=scaler)
        X_b, _, _ = load_and_clean_data(other, scaler=scaler)

        assert torch.allclose(X_a, X_b)

    def test_refitting_on_a_different_file_changes_the_output(self, tmp_path):
        """Proves the scaler actually matters, i.e. reuse is not a no-op."""
        train = tmp_path / "train.txt"
        _write_engine(train, 100, seed=0)
        _, _, scaler = load_and_clean_data(train)

        other = tmp_path / "other.txt"
        _write_engine(other, 100, seed=99)
        X_reused, _, _ = load_and_clean_data(other, scaler=scaler)
        X_refit, _, _ = load_and_clean_data(other)

        assert not torch.allclose(X_reused, X_refit)


class TestPreprocessForInference:
    @pytest.fixture
    def scaler(self, tmp_path):
        f = tmp_path / "train.txt"
        _write_engine(f, 200)
        _, _, s = load_and_clean_data(f)
        return s

    def test_accepts_raw_cmapss_rows(self, scaler):
        """Regression: the API reads uploads with header=None, so a raw
        C-MAPSS row has 26 columns and was rejected outright."""
        rng = np.random.default_rng(0)
        df = pd.DataFrame(
            [[1, c, 0.0, 0.0, 100.0] + list(rng.normal(size=21)) for c in range(1, 61)]
        )

        tensor = preprocess_for_inference(df, scaler)

        assert tensor.shape == (1, 50, 21)

    def test_accepts_bare_sensor_columns(self, scaler):
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(size=(60, 21)))

        tensor = preprocess_for_inference(df, scaler)

        assert tensor.shape == (1, 50, 21)

    def test_accepts_named_sensor_columns(self, scaler):
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(size=(60, 21)), columns=SENSOR_COLUMNS)

        tensor = preprocess_for_inference(df, scaler)

        assert tensor.shape == (1, 50, 21)

    def test_uses_the_supplied_scaler(self, scaler):
        """Regression: serving z-scored the upload instead of MinMax-scaling it."""
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(size=(60, 21)))

        tensor = preprocess_for_inference(df, scaler)

        expected = scaler.transform(df.to_numpy(dtype=np.float32)[-50:])
        assert np.allclose(tensor.squeeze(0).numpy(), expected, atol=1e-5)

    def test_short_history_is_padded_to_a_full_window(self, scaler):
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(size=(10, 21)))

        tensor = preprocess_for_inference(df, scaler)

        assert tensor.shape == (1, 50, 21)

    def test_rejects_wrong_column_count(self, scaler):
        df = pd.DataFrame(np.random.default_rng(0).normal(size=(60, 7)))

        with pytest.raises(ValueError, match="not valid C-MAPSS sensor data"):
            preprocess_for_inference(df, scaler)

    def test_rejects_non_numeric_payload(self, scaler):
        df = pd.DataFrame([["a", "b", "c"]] * 60)

        with pytest.raises(ValueError):
            preprocess_for_inference(df, scaler)


@requires_data
class TestAgainstRealData:
    def test_real_train_target_is_capped(self):
        _, y, _ = load_and_clean_data(TRAIN_FILE)

        assert float(y.max()) == 125.0
        assert float(y.min()) == 0.0

    def test_real_test_target_matches_ground_truth(self):
        """The final window of each engine must equal its published RUL."""
        truth = pd.read_csv(RUL_FILE, sep=r"\s+", header=None, names=["rul"])["rul"].to_numpy()

        X, y, _ = load_and_clean_data(TEST_FILE, rul_file=RUL_FILE)

        # Rebuild the per-engine final targets from the raw file to locate them.
        raw = pd.read_csv(TEST_FILE, sep=r"\s+", header=None, names=RAW_COLUMNS)
        counts = raw.groupby("id")["cycle"].max()
        # An engine contributes len(cycles) - window_size windows, so engines
        # with exactly window_size cycles contribute none and are skipped.
        kept = [eid for eid, n in counts.items() if n > 50]
        last_targets = []
        idx = 0
        for eid in kept:
            n_windows = counts[eid] - 50
            last_targets.append(float(y[idx + n_windows - 1]))
            idx += n_windows

        assert idx == len(y)
        expected = np.minimum(truth[[e - 1 for e in kept]], 125)
        assert np.allclose(last_targets, expected)

    def test_real_feature_count_is_21(self):
        X, _, _ = load_and_clean_data(TRAIN_FILE)

        assert X.shape[2] == 21
