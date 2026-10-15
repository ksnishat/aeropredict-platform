"""Tests for PSI drift detection.

The important properties are: identical distributions give PSI 0, a real shift
gives a large PSI, degenerate inputs do not raise, and the monitor only reports
once it has enough samples to be meaningful.
"""

import json

import numpy as np
import pytest

from src.drift import (
    DriftMonitor,
    classify_psi,
    compute_psi,
)


class TestComputePsi:
    def test_identical_distributions_give_zero(self):
        rng = np.random.default_rng(0)
        sample = rng.normal(0, 1, 5000)
        assert compute_psi(sample, sample) == pytest.approx(0.0, abs=1e-9)

    def test_small_shift_is_stable(self):
        rng = np.random.default_rng(1)
        base = rng.normal(0, 1, 5000)
        shifted = rng.normal(0.1, 1, 5000)
        psi = compute_psi(base, shifted)
        assert psi < 0.10, f"a 0.1-sigma shift should be stable, got {psi}"

    def test_large_shift_is_significant(self):
        rng = np.random.default_rng(2)
        base = rng.normal(0, 1, 5000)
        shifted = rng.normal(1.0, 1, 5000)
        psi = compute_psi(base, shifted)
        assert psi >= 0.25, f"a 1-sigma shift should be significant, got {psi}"

    def test_psi_is_non_negative(self):
        rng = np.random.default_rng(3)
        base = rng.normal(0, 1, 2000)
        for _ in range(5):
            assert compute_psi(base, rng.normal(0, 1, 2000)) >= 0.0

    def test_constant_feature_does_not_raise(self):
        """A sensor that never varies collapses the quantile bins."""
        assert compute_psi(np.ones(100), np.ones(100)) == 0.0

    def test_empty_input_returns_zero(self):
        assert compute_psi([], np.arange(100.0)) == 0.0
        assert compute_psi(np.arange(100.0), []) == 0.0

    def test_nan_values_are_ignored(self):
        base = np.concatenate([np.arange(100.0), [np.nan, np.inf]])
        assert compute_psi(base, np.arange(100.0)) == pytest.approx(0.0, abs=1e-9)


class TestClassifyPsi:
    @pytest.mark.parametrize(
        "psi,expected",
        [
            (0.0, "stable"),
            (0.09, "stable"),
            (0.10, "moderate"),
            (0.24, "moderate"),
            (0.25, "significant"),
            (5.0, "significant"),
        ],
    )
    def test_thresholds(self, psi, expected):
        assert classify_psi(psi) == expected

    def test_non_finite_is_unknown(self):
        assert classify_psi(float("nan")) == "unknown"


class TestDriftMonitor:
    def _baseline(self):
        rng = np.random.default_rng(7)
        return {"s1": rng.normal(0, 1, 1000).tolist()}

    def test_disabled_without_baseline(self):
        monitor = DriftMonitor(baseline={})
        assert monitor.enabled is False
        assert monitor.compute() == {}

    def test_enabled_with_baseline(self):
        assert DriftMonitor(baseline=self._baseline()).enabled is True

    def test_requires_minimum_samples(self):
        """PSI on a handful of points is noise, so it must not be reported."""
        monitor = DriftMonitor(baseline=self._baseline())
        for _ in range(5):
            monitor.observe({"s1": 0.0})
        assert monitor.compute(min_samples=30) == {}

    def test_reports_after_enough_samples(self):
        monitor = DriftMonitor(baseline=self._baseline())
        rng = np.random.default_rng(8)
        for value in rng.normal(0, 1, 200):
            monitor.observe({"s1": float(value)})
        results = monitor.compute(min_samples=30)
        assert "s1" in results
        assert results["s1"]["status"] == "stable"

    def test_detects_a_real_shift(self):
        monitor = DriftMonitor(baseline=self._baseline())
        rng = np.random.default_rng(9)
        for value in rng.normal(3.0, 1, 300):
            monitor.observe({"s1": float(value)})
        results = monitor.compute(min_samples=30)
        assert results["s1"]["status"] == "significant"
        assert monitor.max_psi(min_samples=30) >= 0.25

    def test_unknown_features_are_ignored(self):
        monitor = DriftMonitor(baseline=self._baseline())
        monitor.observe({"not_a_sensor": 1.0})
        assert monitor.compute(min_samples=1) == {}

    def test_non_numeric_values_are_ignored(self):
        monitor = DriftMonitor(baseline=self._baseline())
        monitor.observe({"s1": "not a number"})
        assert monitor.compute(min_samples=1) == {}

    def test_window_is_bounded(self):
        monitor = DriftMonitor(baseline=self._baseline(), window_size=10)
        for i in range(100):
            monitor.observe({"s1": float(i)})
        assert len(monitor._observed["s1"]) == 10

    def test_reset_keeps_baseline(self):
        monitor = DriftMonitor(baseline=self._baseline())
        monitor.observe({"s1": 1.0})
        monitor.reset()
        assert monitor.enabled is True
        assert monitor.compute(min_samples=1) == {}

    def test_round_trip_through_file(self, tmp_path):
        path = tmp_path / "baseline.json"
        monitor = DriftMonitor(baseline=self._baseline())
        monitor.save_baseline(str(path))

        reloaded = DriftMonitor.from_file(str(path))
        assert reloaded.enabled is True
        assert set(reloaded.baseline) == {"s1"}

    def test_missing_file_yields_disabled_monitor(self, tmp_path):
        monitor = DriftMonitor.from_file(str(tmp_path / "absent.json"))
        assert monitor.enabled is False

    def test_saved_baseline_is_valid_json(self, tmp_path):
        path = tmp_path / "baseline.json"
        DriftMonitor(baseline=self._baseline()).save_baseline(str(path))
        payload = json.loads(path.read_text())
        assert "features" in payload
        assert "s1" in payload["features"]


class TestApiDriftScaling:
    """The API must scale uploads before comparing them to the baseline.

    The baseline is captured on MinMax-scaled training data. Comparing raw
    sensor values (hundreds) against it produces a huge spurious PSI for every
    feature, which is exactly the bug these tests pin.
    """

    def _monitor_and_scaler(self):
        from sklearn.preprocessing import MinMaxScaler

        from src.data_preprocessing import SENSOR_COLUMNS
        from src.drift import DriftMonitor

        rng = np.random.default_rng(11)
        scaler = MinMaxScaler()
        # Fit on raw-scale data so transform() maps raw -> 0..1.
        scaler.fit(rng.uniform(600, 700, (500, len(SENSOR_COLUMNS))))

        # The baseline must be the distribution of *window means*, because that
        # is what serving observes. Averaging over the time axis (not the
        # feature axis) is what makes the two comparable.
        n_windows, window = 1000, 50
        raw = rng.uniform(600, 700, (n_windows, window, len(SENSOR_COLUMNS)))
        scaled = scaler.transform(raw.reshape(-1, len(SENSOR_COLUMNS)))
        per_window = scaled.reshape(n_windows, window, len(SENSOR_COLUMNS)).mean(axis=1)

        baseline = {
            name: per_window[:, i].tolist() for i, name in enumerate(SENSOR_COLUMNS)
        }
        return DriftMonitor(baseline=baseline), scaler

    def _raw_frame(self, n=60, seed=None):
        import pandas as pd

        from src.data_preprocessing import SENSOR_COLUMNS

        # A fresh seed per call: reusing one seed would return the identical
        # frame every time, so the 40 "windows" would be 40 copies of a single
        # value and PSI would explode on a degenerate distribution.
        rng = np.random.default_rng(seed if seed is not None else np.random.randint(1 << 30))
        return pd.DataFrame(rng.uniform(600, 700, (n, len(SENSOR_COLUMNS))), columns=SENSOR_COLUMNS)

    def test_raw_upload_is_scaled_before_comparison(self, monkeypatch):
        from src import api

        monitor, scaler = self._monitor_and_scaler()
        monkeypatch.setattr(api, "drift_monitor", monitor)
        monkeypatch.setattr(api, "scaler", scaler)

        # One observation per window, so enough windows are needed to clear the
        # min_samples floor before PSI is reported. PSI is biased upward at
        # small n, so use a realistic sample rather than the bare minimum.
        for _ in range(300):
            result = api.observe_drift(self._raw_frame())
        assert result["recorded"] is True
        assert result["features"], "no features reported after 300 windows"

        # Raw values are ~600-700 while the baseline is 0..1. Without scaling
        # every feature would report a massive PSI.
        worst = max(v["psi"] for v in result["features"].values())
        assert worst < 0.25, f"raw values were compared unscaled (max PSI {worst})"

    def test_one_observation_per_window(self, monkeypatch):
        """A prediction is one window, so it must add exactly one observation.

        The baseline stores one mean per training window. Recording every row of
        the upload would compare a 50-sample slice against a one-mean-per-window
        baseline and inflate PSI.
        """
        from src import api

        monitor, scaler = self._monitor_and_scaler()
        monkeypatch.setattr(api, "drift_monitor", monitor)
        monkeypatch.setattr(api, "scaler", scaler)

        api.observe_drift(self._raw_frame(n=60))
        for name, samples in monitor._observed.items():
            assert len(samples) == 1, f"{name} recorded {len(samples)} observations, expected 1"

    def test_missing_scaler_is_reported_not_raised(self, monkeypatch):
        from src import api

        monitor, _ = self._monitor_and_scaler()
        monkeypatch.setattr(api, "drift_monitor", monitor)
        monkeypatch.setattr(api, "scaler", None)

        result = api.observe_drift(self._raw_frame())
        assert result["recorded"] is False
        assert "scaler" in result["reason"]

    def test_disabled_monitor_returns_early(self, monkeypatch):
        from src import api

        monkeypatch.setattr(api, "drift_monitor", None)
        assert api.observe_drift(self._raw_frame()) == {"enabled": False}

    def test_unrecognised_layout_is_reported(self, monkeypatch):
        import pandas as pd

        from src import api

        monitor, scaler = self._monitor_and_scaler()
        monkeypatch.setattr(api, "drift_monitor", monitor)
        monkeypatch.setattr(api, "scaler", scaler)

        result = api.observe_drift(pd.DataFrame({"a": [1.0], "b": [2.0]}))
        assert result["recorded"] is False
