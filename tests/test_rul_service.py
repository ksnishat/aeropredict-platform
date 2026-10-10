"""Tests for the BentoML RUL prediction service."""
import pytest
from unittest.mock import patch, MagicMock
import numpy as np


class TestBentoMLService:
    """Test the BentoML service definition."""

    def test_service_import(self):
        """Service should import without error."""
        from src.services.rul_service import svc
        assert svc is not None
        assert svc.name == "rul_service"

    def test_service_has_endpoints(self):
        """Service should have prediction endpoints."""
        from src.services.rul_service import svc
        # BentoML services have APIs registered
        assert len(svc.apis) >= 3  # predict_rul, predict_rul_json, health_check

    def test_health_check_endpoint(self):
        """Health check should return service status."""
        from src.services.rul_service import health_check
        result = health_check.func() if hasattr(health_check, 'func') else health_check()
        assert result["status"] == "healthy"
        assert result["service"] == "aeropredict-rul-service"
        assert "model_loaded" in result

    def test_predict_rul_json_with_valid_data(self):
        """JSON prediction should work with valid sensor data."""
        from src.services.rul_service import predict_rul_json

        # Varying sensor readings (constant values normalise to zero and are not realistic)
        input_data = {
            "sensor_data": [
                [0.5 + 0.01 * i + 0.001 * s for s in range(21)]
                for i in range(50)
            ],
            "engine_id": "test_engine"
        }
        result = predict_rul_json.func(input_data) if hasattr(predict_rul_json, 'func') else predict_rul_json(input_data)
        assert "rul" in result
        assert "risk_level" in result
        assert "maintenance_recommendation" in result
        assert "confidence" in result
        assert result["rul"] > 0
        assert result["risk_level"] in ("LOW", "WARNING", "CRITICAL")

    def test_predict_rul_json_missing_data(self):
        """JSON prediction should handle missing data."""
        from src.services.rul_service import predict_rul_json

        result = predict_rul_json.func({}) if hasattr(predict_rul_json, 'func') else predict_rul_json({})
        assert "error" in result

    def test_batch_predict(self):
        """Batch prediction should work with multiple sequences."""
        from src.services.rul_service import batch_predict

        input_data = {
            "sensor_data_list": [
                [[0.5] * 21 for _ in range(50)],
                [[0.3] * 21 for _ in range(50)],
            ]
        }
        result = batch_predict.func(input_data) if hasattr(batch_predict, 'func') else batch_predict(input_data)
        assert "predictions" in result
        assert "count" in result
        assert result["count"] == 2
        assert len(result["predictions"]) == 2

    def test_batch_predict_missing_data(self):
        """Batch prediction should handle missing data."""
        from src.services.rul_service import batch_predict

        result = batch_predict.func({}) if hasattr(batch_predict, 'func') else batch_predict({})
        assert "error" in result


class TestInputSchemas:
    """Test Pydantic input schemas."""

    def test_sensor_data_input_schema(self):
        """SensorDataInput should validate correctly."""
        from src.services.rul_service import SensorDataInput

        data = {
            "sensor_data": [[0.5] * 21 for _ in range(50)],
            "engine_id": "engine_001"
        }
        input_obj = SensorDataInput(**data)
        assert input_obj.engine_id == "engine_001"
        assert len(input_obj.sensor_data) == 50

    def test_prediction_response_schema(self):
        """PredictionResponse should validate correctly."""
        from src.services.rul_service import PredictionResponse

        response = PredictionResponse(
            rul=150,
            risk_level="LOW",
            maintenance_recommendation="Normal operation",
            confidence=0.95
        )
        assert response.rul == 150
        assert response.risk_level == "LOW"
