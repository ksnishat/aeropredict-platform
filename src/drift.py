"""Population Stability Index (PSI) drift detection.

PSI compares the distribution of a feature at training time (the baseline)
against the distribution observed in production. It is the standard metric for
tabular model monitoring because it is cheap, needs no ground-truth labels, and
has widely used interpretation thresholds:

    PSI < 0.10   no meaningful shift
    PSI < 0.25   moderate shift, investigate
    PSI >= 0.25  significant shift, the model is out of distribution

The baseline is captured during training and persisted next to the model, so
serving can compare live inputs against the exact distribution the model was
fitted on rather than a hardcoded guess.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import numpy as np

# Interpretation thresholds, per the conventional PSI bands.
PSI_STABLE = 0.10
PSI_MODERATE = 0.25


def _bin_edges(baseline: np.ndarray, bins: int) -> np.ndarray:
    """Return quantile bin edges from the baseline distribution.

    Quantile bins keep roughly equal counts per bin, which matters because PSI
    is unstable when a bin is nearly empty in the baseline.
    """
    edges = np.quantile(baseline, np.linspace(0, 1, bins + 1))
    # Degenerate features (a constant sensor) collapse the quantiles to one
    # value; widen slightly so np.histogram has valid, increasing edges.
    edges = np.unique(edges)
    if edges.size < 2:
        edges = np.array([edges[0] - 0.5, edges[0] + 0.5])
    return edges


def _proportions(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(values, bins=edges)
    total = counts.sum()
    if total == 0:
        return np.zeros(len(counts), dtype=float)
    return counts.astype(float) / total


def compute_psi(
    baseline: np.ndarray,
    current: np.ndarray,
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """Compute PSI between a baseline sample and a current sample.

    Args:
        baseline: Feature values observed during training.
        current: Feature values observed in production.
        bins: Number of quantile bins.
        epsilon: Floor applied to zero proportions so the log stays finite.

    Returns:
        The PSI as a non-negative float. Returns 0.0 when either sample is empty.
    """
    baseline = np.asarray(baseline, dtype=float).ravel()
    current = np.asarray(current, dtype=float).ravel()
    baseline = baseline[np.isfinite(baseline)]
    current = current[np.isfinite(current)]

    if baseline.size == 0 or current.size == 0:
        return 0.0

    edges = _bin_edges(baseline, bins)
    baseline_pct = _proportions(baseline, edges)
    current_pct = _proportions(current, edges)

    # Clip both sides away from zero: PSI is undefined when a bin has no
    # baseline mass, and clipping is what the standard implementation does.
    baseline_pct = np.clip(baseline_pct, epsilon, None)
    current_pct = np.clip(current_pct, epsilon, None)

    return float(np.sum((current_pct - baseline_pct) * np.log(current_pct / baseline_pct)))


def classify_psi(psi: float) -> str:
    """Map a PSI value to an interpretation label."""
    if not math.isfinite(psi):
        return "unknown"
    if psi < PSI_STABLE:
        return "stable"
    if psi < PSI_MODERATE:
        return "moderate"
    return "significant"


class DriftMonitor:
    """Tracks a rolling window of feature values and compares them to a baseline.

    One monitor covers all features: ``observe`` takes a mapping of feature name
    to value and the monitor accumulates per-feature samples.
    """

    def __init__(
        self,
        baseline: dict[str, list[float]] | None = None,
        window_size: int = 500,
        bins: int = 10,
    ) -> None:
        self.baseline: dict[str, list[float]] = baseline or {}
        self.window_size = window_size
        self.bins = bins
        self._observed: dict[str, list[float]] = {name: [] for name in self.baseline}

    @classmethod
    def from_file(cls, path: str, **kwargs: Any) -> DriftMonitor:
        """Load a persisted baseline. Returns an empty monitor if absent."""
        if not os.path.exists(path):
            return cls(baseline={}, **kwargs)
        with open(path) as fh:
            payload = json.load(fh)
        return cls(baseline=payload.get("features", {}), **kwargs)

    def save_baseline(self, path: str) -> None:
        """Persist the baseline so serving can reuse the training distribution."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"features": self.baseline}, fh)

    @property
    def enabled(self) -> bool:
        """True when a baseline is loaded, i.e. PSI can actually be computed."""
        return bool(self.baseline)

    def observe(self, features: dict[str, float]) -> None:
        """Record one production observation."""
        for name, value in features.items():
            if name not in self.baseline:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric):
                continue
            samples = self._observed[name]
            samples.append(numeric)
            if len(samples) > self.window_size:
                del samples[0]

    def compute(self, min_samples: int = 30) -> dict[str, dict[str, Any]]:
        """Compute per-feature PSI.

        Args:
            min_samples: Skip features with fewer observations than this. PSI on
                a handful of points is noise, not signal. Note that PSI is also
                biased upward at small sample sizes even above this floor: with
                10 quantile bins, a few dozen observations leave some bins
                sparse. Treat a single reading as a hint and watch the trend.

        Returns:
            Mapping of feature name to ``{"psi", "status", "samples"}``.
        """
        results: dict[str, dict[str, Any]] = {}
        for name, base_values in self.baseline.items():
            observed = self._observed.get(name, [])
            if len(observed) < min_samples:
                continue
            psi = compute_psi(
                np.asarray(base_values), np.asarray(observed), bins=self.bins
            )
            results[name] = {
                "psi": round(psi, 6),
                "status": classify_psi(psi),
                "samples": len(observed),
            }
        return results

    def max_psi(self, min_samples: int = 30) -> float:
        """Return the largest PSI across features, or 0.0 when nothing to report."""
        results = self.compute(min_samples=min_samples)
        if not results:
            return 0.0
        return max(item["psi"] for item in results.values())

    def reset(self) -> None:
        """Clear the observed window, keeping the baseline."""
        self._observed = {name: [] for name in self.baseline}
