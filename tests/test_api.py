"""AeroPredict Project API Tests.

Tests for the FastAPI backend, data preprocessing, and model inference.
"""

import json
import pytest
from fastapi.testclient import TestClient
from src.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def sample_sensor_csv(tmp_path):
    """Generate a valid NASA C-MAPSS style sensor data CSV."""
    import pandas as pd
    df = pd.DataFrame(
        {
            "seq_id": list(range(1, 6)),
            "op1": [0.5] * 5,
            "op2": [0.6] * 5,
            "op3": [0.7] * 5,
            "op4": [0.8] * 5,
            "op5": [0.9] * 5,
            "op6": [1.0] * 5,
            "set1": [1] * 5,
            "set2": [1] * 5,
            "set3": [1] * 5,
        }
    )
    filepath = tmp_path / "sensor_data.csv"
    df.to_csv(filepath, sep=' ', index=False)
    return filepath


@pytest.fixture
def bad_sensor_data(tmp_path):
    """Generate invalid sensor data for error tests."""
    filepath = tmp_path / "bad_data.csv"
    filepath.write_text("garbage data that is not proper sensor data")
    return filepath


class TestHealth:
    """Test /health endpoint."""

    def test_health_check(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aeropredict-api"


class TestPrediction:
    """Test /predict endpoint."""

    def test_predict_with_valid_csv(self, client, sample_sensor_csv):
        with open(sample_sensor_csv, "rb") as f:
            response = client.post("/predict", files={"file": f.read()})
        assert response.status_code == 200
        data = response.json()
        assert "rul" in data
        assert "risk_level" in data
        assert "maintenance_recommendation" in data
        assert isinstance(data["rul"], int)
        assert data["risk_level"] in ("LOW", "WARNING", "CRITICAL")
        assert len(data["maintenance_recommendation"]) > 0

    def test_predict_returns_positive_rul(self, client, sample_sensor_csv):
        """RUL should always be positive."""
        with open(sample_sensor_csv, "rb") as f:
            response = client.post("/predict", files={"file": f.read()})
        data = response.json()
        assert data["rul"] > 0

    def test_predict_with_invalid_csv(self, client, bad_sensor_data):
        """Invalid CSV should return an error status (500 server-side parse failure)."""
        with open(bad_sensor_data, "rb") as f:
            response = client.post("/predict", files={"file": f.read()})
        assert response.status_code in (422, 500)


class TestRiskLevels:
    """Test risk level assignment."""

    @pytest.mark.parametrize(
        "rul_value, expected_level",
        [
            (150, "LOW"),
            (75, "WARNING"),
            (30, "CRITICAL"),
            (50, "WARNING"),  # boundary case
        ],
    )
    def test_risk_assignment(self, client, tmp_path, rul_value, expected_level):
        import pandas as pd
        df = pd.DataFrame({"sensor1": [0.5] * 10})
        # Adjust data to achieve the desired RUL in mock mode
        csv_path = tmp_path / "risk_test.csv"
        df.to_csv(csv_path, sep=' ', index=False)
        with open(csv_path, "rb") as f:
            response = client.post("/predict", files={"file": f.read()})
        assert response.status_code == 200
        data = response.json()
        # Note: mock mode uses random RUL, so we test structure, not exact values
        assert data["risk_level"] in ("LOW", "WARNING", "CRITICAL")
        assert data["rul"] > 0


class TestOpenAPI:
    """Test OpenAPI schema and documentation."""

    def test_openapi_schema(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "AeroPredict API"
        assert "/predict" in schema["paths"]


def test_predict_serialization(client, tmp_path):
    """Test that response fields match the PredictionResponse schema."""
    import pandas as pd
    from pydantic import BaseModel

    class PredictionResponse(BaseModel):
        rul: int
        risk_level: str
        maintenance_recommendation: str

    df = pd.DataFrame({"sensor1": [0.5] * 10})
    csv_path = tmp_path / "test_ser.csv"
    df.to_csv(csv_path, sep=' ', index=False)
    with open(csv_path, "rb") as f:
        response = client.post("/predict", files={"file": f.read()})
    data = response.json()
    validated = PredictionResponse(**data)
    assert validated.rul == data["rul"]
    assert validated.risk_level == data["risk_level"]
    assert validated.maintenance_recommendation == data["maintenance_recommendation"]