# AeroPredict API Documentation

> OpenAPI 3.0 Specification for AeroPredict Backend API

## Base URL

```
http://localhost:8000
```

## Authentication

The API does not require authentication for public endpoints. For production deployments, consider adding API key authentication or OAuth2.

## Endpoints

### `GET /`

Root endpoint - returns service information.

**Response Schema:**

```json
{
  "service": "string",
  "version": "string",
  "description": "string"
}
```

**Example Request:**
```bash
curl http://localhost:8000/
```

**Example Response:**
```json
{
  "service": "AeroPredict API",
  "version": "1.0.0",
  "description": "Predictive Maintenance RUL API"
}
```

---

### `GET /health`

Health check endpoint with model loading status.

**Response Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Service status ("healthy") |
| `model_loaded` | boolean | Whether the RUL model is loaded |
| `timestamp` | string | ISO format timestamp |

**Example Response:**
```json
{
  "status": "healthy",
  "model_loaded": true,
  "timestamp": "2026-10-02T10:30:00.000Z"
}
```

---

### `POST /predict`

Predict Remaining Useful Life (RUL) for engine sensor data.

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `engine_id` | string | No | Engine identifier (auto-generated if not provided) |
| `sensor_data` | array[float] | Yes | Sensor readings array (21 sensors) |
| `cycle` | integer | No | Current engine cycle |
| `time_step` | integer | No | Time step within cycle |
| `settings` | array[float] | No | Operating settings (3 values) |

**Response Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `engine_id` | string | Engine identifier |
| `predicted_rul` | float | Predicted Remaining Useful Life in cycles |
| `risk_level` | string | Risk classification ("low", "medium", "high") |
| `confidence` | float | Model confidence score (0.0 to 1.0) |
| `processing_time_ms` | float | Processing time in milliseconds |
| `timestamp` | string | ISO format timestamp |

**Example Request:**
```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "sensor_data": [340.0, 84.0, 0.42, 392.0, 392.0, 3.2, 392.0, 392.0, 0.4, 392.0, 0.1, 392.0, 0.0934, 340.0, 84.0, 0.23, 340.0, 84.0, 0.02, 392.0, 392.0],
    "cycle": 150,
    "settings": [0.459, 0.00288, 20.0
  }'
```

**Example Response:**
```json
{
  "engine_id": "eng_001",
  "predicted_rul": 45.2,
  "risk_level": "medium",
  "confidence": 0.92,
  "processing_time_ms": 15.3,
  "timestamp": "2026-10-02T10:30:00.000Z"
}
```

**Risk Levels:**
| Level | RUL Range | Description |
|-------|-----------|-------------|
| low | > 50 cycles | Engine healthy, no immediate action needed |
| medium | 20-50 cycles | Scheduled maintenance recommended |
| high | < 20 cycles | Immediate maintenance required |

---

### `GET /predict/rul/{engine_id}`

Retrieve RUL prediction for a specific engine.

**Parameters:**

| Parameter | In | Type | Description |
|-----------|----|------|------------|
| `engine_id` | path | string | Engine identifier |

**Response:** Same schema as POST /predict

---

### `POST /diagnostics`

Generate AI-powered diagnostics for engine sensor data.

**Request Body:** Same as `/predict`

**Response Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `engine_id` | string | Engine identifier |
| `diagnoses` | array[object] | List of diagnoses |
| `maintenance_recommendation` | string | AI-generated recommendation |
| `processing_time_ms` | float | Processing time in ms |
| `timestamp` | string | ISO format timestamp |

**Diagnosis Object:**
| Field | Type | Description |
|-------|------|-------------|
| `component` | string | Component name (e.g., "HPC", "HPT", "EGT") |
| `condition` | string | Condition description |
| `severity` | string | Severity level (low/medium/high) |
| `confidence` | float | Confidence score |

---

### `POST /report`

Generate a comprehensive maintenance report using RAG.

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `engine_id` | string | No | Engine identifier |
| `sensor_data` | array[float] | Yes | Sensor readings |
| `language` | string | No | Report language ("en" or "de") |

**Response Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `engine_id` | string | Engine identifier |
| `report` | string | Generated report text |
| `language` | string | Report language |
| `processing_time_ms` | float | Processing time in ms |
| `timestamp` | string | ISO format timestamp |

---

### `GET /docs`

Interactive API documentation (Swagger UI).

### `GET /openapi.json`

OpenAPI specification in JSON format.

## Error Responses

### `400 Bad Request`
```json
{
  "detail": "Invalid sensor data format"
}
```

### `500 Internal Server Error`
```json
{
  "detail": "Model inference failed: [error message]"
}
```

### `503 Service Unavailable`
```json
{
  "detail": "Model not loaded. Please check model registry."
}
```

## Metrics

The API exposes Prometheus-compatible metrics at `GET /metrics`:

- `aeropredict_rul_prediction_seconds` - RUL prediction latency histogram
- `aeropredict_rag_query_seconds` - RAG query latency histogram
- `aeropredict_model_loaded` - Model loaded status gauge (1=loaded, 0=not)
- `aeropredict_api_requests_total` - Total API request counter
- `aeropredict_api_errors_total` - Total API error counter