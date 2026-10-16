"""Tests for the DVC pipeline scripts.

These scripts were previously untested, which is why a self-referential
assignment in extract_bronze.py (`output_path = Path(output_path)`) survived
and raised NameError on every call.
"""

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = REPO_ROOT / "src" / "scripts" / "extract_bronze.py"
TRANSFORM = REPO_ROOT / "src" / "scripts" / "transform_silver.py"
FEATURE = REPO_ROOT / "src" / "scripts" / "feature_engineer_gold.py"

SENSOR_COLS = [f"s{i}" for i in range(1, 22)]


def _write_cmapss(path: Path, n_rows: int = 30, engines: int = 2):
    """Write a minimal C-MAPSS-shaped text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for engine in range(1, engines + 1):
        for cycle in range(1, n_rows // engines + 1):
            sensors = " ".join(str(500.0 + i) for i in range(21))
            lines.append(f"{engine} {cycle} 0.0 0.0 100.0 {sensors}")
    path.write_text("\n".join(lines) + "\n")


def _run(script: Path, *args):
    # The scripts import `src.*`, so the repo root must be on PYTHONPATH.
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


class TestExtractBronze:
    def test_extracts_a_dataset(self, tmp_path):
        """Regression: this raised NameError on every call."""
        src = tmp_path / "raw"
        _write_cmapss(src / "train_FD001.txt")
        out = tmp_path / "bronze"

        result = _run(EXTRACT, "--source", str(src), "--output", str(out), "--dataset", "FD001")

        assert result.returncode == 0, result.stderr
        assert (out / "FD001_train.csv").exists()

    def test_missing_source_raises(self, tmp_path):
        result = _run(
            EXTRACT, "--source", str(tmp_path / "nope"), "--output", str(tmp_path / "out")
        )
        assert result.returncode != 0
        assert "Source directory not found" in result.stderr


class TestTransformSilver:
    def test_cleans_and_writes_output(self, tmp_path):
        bronze = tmp_path / "bronze"
        _write_cmapss(bronze / "train_FD001.txt")
        silver = tmp_path / "silver"
        silver.mkdir()

        # Produce the bronze CSV the silver stage expects.
        _run(EXTRACT, "--source", str(bronze), "--output", str(bronze), "--dataset", "FD001")

        result = _run(TRANSFORM, "--input", str(bronze), "--output", str(silver))

        assert result.returncode == 0, result.stderr
        assert (silver / "FD001_train.csv").exists()

    def test_fills_nan_without_the_deprecated_method_argument(self, tmp_path):
        """Regression: fillna(method=...) is deprecated and will be removed."""
        from src.scripts.transform_silver import clean_dataset

        bronze = tmp_path / "bronze"
        bronze.mkdir()
        df = pd.DataFrame(
            {
                "id": [1, 1, 1],
                "cycle": [1, 2, 3],
                **{c: [1.0, None, 3.0] for c in SENSOR_COLS},
            }
        )
        df.to_csv(bronze / "FD001_train.csv", index=False)
        out = tmp_path / "silver"
        out.mkdir()

        with _no_warnings():
            quality = clean_dataset(bronze, out, "FD001")

        assert quality is not None
        assert quality["nan_values"] > 0
        cleaned = pd.read_csv(out / "FD001_train.csv")
        assert not cleaned.isnull().any().any()

    def test_drops_duplicate_rows(self, tmp_path):
        from src.scripts.transform_silver import clean_dataset

        bronze = tmp_path / "bronze"
        bronze.mkdir()
        row = {"id": 1, "cycle": 1, **dict.fromkeys(SENSOR_COLS, 1.0)}
        pd.DataFrame([row, row, row]).to_csv(bronze / "FD001_train.csv", index=False)
        out = tmp_path / "silver"
        out.mkdir()

        quality = clean_dataset(bronze, out, "FD001")

        assert quality["duplicates_removed"] == 2
        assert quality["final_rows"] == 1

    def test_missing_input_returns_none(self, tmp_path):
        from src.scripts.transform_silver import clean_dataset

        out = tmp_path / "silver"
        out.mkdir()
        assert clean_dataset(tmp_path / "empty", out, "FD001") is None


class TestFeatureEngineerGold:
    def test_produces_sequences_and_labels(self, tmp_path):
        """Regression: the function logged the row count but the variable was
        dead, and the module had no test at all."""
        from src.scripts.feature_engineer_gold import engineer_features

        silver = tmp_path / "silver"
        silver.mkdir()
        rows = []
        for engine in (1, 2):
            for cycle in range(1, 61):
                rows.append(
                    {
                        "id": engine,
                        "cycle": cycle,
                        **{c: float(cycle) for c in SENSOR_COLS},
                    }
                )
        pd.DataFrame(rows).to_csv(silver / "FD001_train.csv", index=False)
        gold = tmp_path / "gold"
        gold.mkdir()

        result = engineer_features(silver, gold, "FD001")

        assert result is not None
        # The function writes its artefacts into the gold directory.
        assert any(gold.iterdir()), "no artefacts written to the gold layer"

    def test_missing_input_returns_none(self, tmp_path):
        from src.scripts.feature_engineer_gold import engineer_features

        gold = tmp_path / "gold"
        gold.mkdir()
        assert engineer_features(tmp_path / "empty", gold, "FD001") is None


class _no_warnings:
    """Context manager that fails if a FutureWarning is raised."""

    def __enter__(self):
        import warnings

        self._ctx = warnings.catch_warnings()
        self._ctx.__enter__()
        warnings.simplefilter("error", FutureWarning)
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


class TestTrainingCli:
    """The training script must be runnable without arguments.

    train() previously defaulted data_path to the container mount
    (/opt/airflow/data/train_FD001.txt) and __main__ called train() with no
    arguments, so `python src/train_model.py` failed with FileNotFoundError on
    any host checkout. There were no flags to override it either.
    """

    def test_default_data_path_resolves_on_a_host_checkout(self):
        from src.train_model import _default_data_path

        path = _default_data_path()
        assert os.path.exists(path), f"resolved to a missing path: {path}"
        assert path.endswith("train_FD001.txt")

    def test_default_data_path_prefers_the_env_override(self, tmp_path, monkeypatch):
        from src.train_model import _default_data_path

        fake = tmp_path / "train_FD001.txt"
        fake.write_text("1 1 1\n")
        monkeypatch.setenv("AEROPREDICT_DATA", str(fake))
        assert _default_data_path() == str(fake)

    def test_cli_exposes_every_train_parameter(self):
        """A parameter with no flag can only be changed by editing the source."""
        import inspect
        import subprocess
        import sys

        from src.train_model import train

        result = subprocess.run(
            [sys.executable, "src/train_model.py", "--help"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0, result.stderr
        help_text = result.stdout

        params = set(inspect.signature(train).parameters)
        # argparse renders underscores as dashes
        expected = {p.replace("_", "-") for p in params}
        missing = {p for p in expected if f"--{p}" not in help_text}
        assert not missing, f"train() parameters with no CLI flag: {sorted(missing)}"

    def test_train_resolves_the_path_instead_of_hardcoding_it(self):
        """The default must be None so resolution happens at call time.

        A hardcoded default of "/opt/airflow/data/train_FD001.txt" is what made
        the script fail on a host checkout, even though _default_data_path()
        existed and worked.
        """
        import inspect

        from src.train_model import train

        default = inspect.signature(train).parameters["data_path"].default
        assert default is None, (
            f"data_path defaults to {default!r}; it must be None so "
            "_default_data_path() is consulted"
        )
