"""
AeroPredict API - FastAPI backend for RUL prediction with GenAI diagnostics.

Endpoints:
- GET / : Health check
- POST /predict : Upload sensor data, get RUL prediction + maintenance report
"""

import asyncio
import io
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

import joblib
import numpy as np
import pandas as pd
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel

# Allow `python src/api.py` as well as `uvicorn src.api:app`.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from auth import auth_enabled, require_api_key
from data_preprocessing import preprocess_for_inference
from ratelimit import PREDICT_LIMIT, READ_LIMIT, limiter
from ratelimit import install as install_rate_limit
from uploads import enforce_content_length, read_upload
from versioning import install as install_versioning

# Prometheus instrumentation
try:
    from prometheus_client import Counter, Gauge, Histogram

    PREDICTION_COUNT = Counter(
        "aeropredict_predictions_total",
        "Total RUL predictions served",
        ["risk_level"],
    )
    PREDICTION_LATENCY = Histogram(
        "aeropredict_prediction_latency_seconds",
        "RUL prediction latency in seconds",
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    PREDICTED_RUL = Gauge(
        "aeropredict_last_rul_value",
        "Most recent predicted Remaining Useful Life (cycles)",
    )
    MODEL_LOADED_GAUGE = Gauge(
        "aeropredict_model_loaded",
        "1 if the trained model is loaded, 0 if fallback baseline is used",
    )
    RAG_AVAILABLE_GAUGE = Gauge(
        "aeropredict_rag_available",
        "1 if the ChromaDB retrieval index is ready, 0 if reports use the template",
    )
    RAG_REPORT_COUNT = Counter(
        "aeropredict_reports_total",
        "Maintenance reports generated, by source",
        ["source"],
    )
    RAG_MODEL_MISMATCH = Gauge(
        "aeropredict_rag_model_mismatch",
        "1 when the configured OLLAMA_MODEL was not pulled and a substitute was "
        "used, 0 when the configured tag was found",
    )
    DRIFT_PSI_GAUGE = Gauge(
        "aeropredict_feature_drift_psi",
        "Population Stability Index per sensor vs the training baseline",
        ["feature"],
    )
    DRIFT_MAX_PSI_GAUGE = Gauge(
        "aeropredict_max_drift_psi",
        "Largest per-feature PSI in the current window",
    )
except ImportError:  # pragma: no cover - prometheus_client optional at import time
    PREDICTION_COUNT = PREDICTION_LATENCY = PREDICTED_RUL = None
    MODEL_LOADED_GAUGE = RAG_AVAILABLE_GAUGE = RAG_REPORT_COUNT = None
    RAG_MODEL_MISMATCH = None
    DRIFT_PSI_GAUGE = DRIFT_MAX_PSI_GAUGE = None

# Try to import MLflow model loading
try:
    import mlflow
    import mlflow.pytorch
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

# Try to import PyTorch for model loading
try:
    import torch
    PYTORCH_AVAILABLE = True
except ImportError:
    PYTORCH_AVAILABLE = False

# Global model state
model = None
model_loaded = False
scaler = None
rag_agent = None
rag_model_name = None
rag_model_warning = None
drift_monitor = None


def load_model():
    """Load the trained LSTM model and its feature scaler.

    The scaler is not optional: the model was trained on MinMax-scaled sensor
    readings, so serving must apply the identical transform. Without it the
    model receives out-of-distribution inputs and its output is meaningless.
    """
    global model, model_loaded, scaler

    model_path = os.getenv("MODEL_PATH", "models/lstm_best.pt")
    scaler_path = os.getenv("SCALER_PATH", os.path.join(os.path.dirname(model_path), "scaler.joblib"))

    if os.path.exists(scaler_path):
        try:
            scaler = joblib.load(scaler_path)
            print(f"Scaler loaded from {scaler_path}")
        except Exception as e:
            print(f"Scaler loading failed: {e}")
            scaler = None
    else:
        print(f"No scaler found at {scaler_path}; falling back to baseline predictions")

    # Try MLflow first
    if MLFLOW_AVAILABLE:
        try:
            mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
            model = mlflow.pytorch.load_model("models/lstm_best.pt")
            model_loaded = True
            print("Model loaded from MLflow")
            return True
        except Exception as e:
            print(f"MLflow model loading failed: {e}")

    # Try local PyTorch path
    if PYTORCH_AVAILABLE and os.path.exists(model_path):
        try:
            model = torch.jit.load(model_path, map_location="cpu")
            model_loaded = True
            print("Model loaded from local path")
            return True
        except Exception as e:
            print(f"Local model loading failed: {e}")

    # Fallback: use baseline
    print("No model found, using baseline (mock predictions)")
    model_loaded = False
    return False


def load_rag():
    """Build the ChromaDB retrieval index used for maintenance reports.

    Best-effort: the API must still serve predictions when the index or the
    embedding model is unavailable, so a failure here only downgrades reports
    to the template.
    """
    global rag_agent, rag_model_name, rag_model_warning

    try:
        from rag_inference import MaintenanceRAG

        agent = MaintenanceRAG()
        count = agent.ingest_knowledge()
        rag_agent = agent
        rag_model_name = agent.llm_model_name
        rag_model_warning = agent.model_warning
        print(f"RAG index ready ({count} chunks), LLM={rag_model_name}")
        return True
    except Exception as e:  # noqa: BLE001 - reports degrade to the template
        print(f"RAG unavailable ({e}); maintenance reports will use the template")
        rag_agent = None
        rag_model_name = None
        rag_model_warning = None
        return False


def load_drift_monitor():
    """Load the training-time feature baseline used for PSI drift detection.

    Best-effort: without a baseline the API still serves predictions, it just
    cannot report drift.
    """
    global drift_monitor

    try:
        from drift import DriftMonitor

        path = os.getenv("DRIFT_BASELINE_PATH", "models/drift_baseline.json")
        monitor = DriftMonitor.from_file(path)
        if not monitor.enabled:
            print(f"No drift baseline at {path}; drift metrics disabled")
            drift_monitor = None
            return False
        drift_monitor = monitor
        print(f"Drift baseline loaded ({len(monitor.baseline)} features)")
        return True
    except Exception as e:  # noqa: BLE001 - drift reporting is optional
        print(f"Drift baseline unavailable ({e})")
        drift_monitor = None
        return False


def predict_rul_from_data(df: pd.DataFrame) -> int:
    """Make RUL prediction from sensor data DataFrame.

    Raises:
        ValueError: if the uploaded data cannot be interpreted as numeric sensor readings.
    """
    global model, model_loaded, scaler

    if model_loaded and model is not None and scaler is not None:
        try:
            tensor = preprocess_for_inference(df, scaler)
            with torch.no_grad():
                pred = model(tensor).item()
            return max(0, int(round(pred)))
        except ValueError:
            raise
        except Exception as e:
            print(f"Model inference failed: {e}")

    # Baseline fallback: reject payloads that are not sensor data at all so the
    # caller still gets a 422 rather than a fabricated number.
    numeric_df = df.apply(pd.to_numeric, errors="coerce")
    if numeric_df.isna().all().all() or numeric_df.empty:
        raise ValueError(
            "Uploaded file is not valid C-MAPSS sensor data (no numeric columns found)."
        )
    numeric_df = numeric_df.dropna(axis=1, how="all").fillna(0.0)

    variance = float(numeric_df.var().mean())
    baseline_rul = max(20, int(150 - variance * 100))
    return baseline_rul


def calculate_risk_level(rul: int) -> str:
    """Determine risk level based on RUL value."""
    if rul < 50:
        return "CRITICAL"
    elif rul < 100:
        return "WARNING"
    return "LOW"


def _generate_report_blocking(rul: int) -> tuple[str, str]:
    """Synchronous report generation. See ``get_genai_report`` for the async wrapper."""
    global rag_agent

    if rag_agent is not None:
        try:
            return rag_agent.generate_report(float(rul)), "rag"
        except Exception as exc:  # noqa: BLE001 - a report must always be returned
            print(f"RAG report generation failed ({exc}); using template fallback")

    return _template_report(rul), "template"


async def get_genai_report(rul: int) -> tuple[str, str]:
    """Generate a maintenance report for a predicted RUL.

    Uses the ChromaDB retrieval index over the turbofan maintenance reference
    and a local LLM. Returns ``(report, source)`` where source is ``"rag"`` or
    ``"template"``.

    Never raises: if the index or the LLM is unavailable the caller still gets
    a usable report, and the ``source`` label makes the degradation visible in
    Prometheus rather than silent.

    The LLM call is synchronous and takes seconds, so it runs in a worker thread.
    Calling it directly from the async handler blocked the event loop: a load
    test at concurrency 6 measured p50 12.2s, because every request queued behind
    the one holding the loop. Offloading it lets requests overlap.
    """
    return await asyncio.to_thread(_generate_report_blocking, rul)


def _template_report(rul: int) -> str:
    """Deterministic report used when retrieval or the LLM is unavailable."""
    if rul < 50:
        return "High Urgency: Efficiency Loss detected in HPC module."
    return "Normal Operation: Standard maintenance recommended."


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load model at startup."""
    load_model()
    load_rag()
    load_drift_monitor()
    # Publish the gauges at startup. Setting them only inside /predict means the
    # metrics read 0 until the first request, so a dashboard shows "model not
    # loaded" on a healthy service that simply has not been called yet.
    if MODEL_LOADED_GAUGE is not None:
        MODEL_LOADED_GAUGE.set(1 if model_loaded else 0)
    if RAG_AVAILABLE_GAUGE is not None:
        RAG_AVAILABLE_GAUGE.set(1 if rag_agent is not None else 0)
        RAG_MODEL_MISMATCH.set(1 if rag_model_warning else 0)
    yield


# Initialize FastAPI
app = FastAPI(
    title="AeroPredict API",
    version="1.0",
    description="RUL Prediction for NASA C-MAPSS with GenAI Diagnostics",
    lifespan=lifespan,
)
install_rate_limit(app)


# Input Schema
class PredictionResponse(BaseModel):
    rul: int
    risk_level: str
    maintenance_recommendation: str
    drift: dict | None = None
    # Present only when the caller asked for the report to be generated in the
    # background. Poll /reports/{report_id} for the result.
    report_id: str | None = None
    report_status: str | None = None


class ReportResponse(BaseModel):
    report_id: str
    status: str
    report: str | None = None
    source: str | None = None


# In-memory store for asynchronously generated reports.
#
# Deliberately bounded and process-local: this is a single-process demo service,
# and an unbounded dict would leak memory under sustained load. A real
# deployment would use Redis or a database, and would survive a restart. The
# bound is what keeps the failure mode "old reports expire" rather than "the
# process runs out of memory".
_REPORT_STORE: dict[str, dict] = {}
_REPORT_STORE_MAX = 256


def _store_report(report_id: str, report: str, source: str) -> None:
    """Record a finished report, evicting the oldest entry when full."""
    if len(_REPORT_STORE) >= _REPORT_STORE_MAX:
        # dicts preserve insertion order, so the first key is the oldest.
        oldest = next(iter(_REPORT_STORE))
        _REPORT_STORE.pop(oldest, None)
    _REPORT_STORE[report_id] = {"status": "ready", "report": report, "source": source}


def _generate_report_task(report_id: str, rul: int) -> None:
    """Background worker: generate the report and store it.

    Runs in a thread pool via BackgroundTasks, so the slow LLM call never
    touches the event loop.
    """
    try:
        report, source = _generate_report_blocking(rul)
        _store_report(report_id, report, source)
        if RAG_REPORT_COUNT is not None:
            RAG_REPORT_COUNT.labels(source=source).inc()
    except Exception as exc:  # noqa: BLE001 - a failed report must not crash
        print(f"Background report generation failed ({exc})")
        _store_report(report_id, _template_report(rul), "template")


@app.get("/")
def health_check():
    return {
        "status": "healthy",
        "service": "aeropredict-api",
        "model_loaded": model_loaded,
        "rag_available": rag_agent is not None,
        "rag_model": rag_model_name,
        "rag_model_warning": rag_model_warning,
        "auth_enabled": auth_enabled(),
    }


@app.post("/predict", response_model=PredictionResponse,
          dependencies=[Depends(require_api_key), Depends(enforce_content_length)])
@limiter.limit(PREDICT_LIMIT)
async def predict_rul(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    async_report: bool = False,
):
    """
    Predict RUL from uploaded sensor data CSV.

    Expected format: NASA C-MAPSS style (space-separated, no header).
    Returns: RUL cycles, risk level, and maintenance recommendation.

    ``async_report=true`` returns the prediction immediately and generates the
    maintenance report in the background. Poll ``/reports/{report_id}`` for it.

    Why this exists: the RUL prediction takes ~50 ms, but the LLM report takes
    1.2-2.1 s. Serving both in one response gated a fast, deterministic
    prediction behind a slow, non-deterministic one — a load test at concurrency
    6 measured p50 10.3 s, because Ollama serialises requests. With
    ``async_report=true`` the prediction path is no longer bounded by the LLM.
    """
    try:
        # 1. READ DATA
        contents = await read_upload(file)
        df = pd.read_csv(io.BytesIO(contents), sep=r"\s+", header=None)

        # 2. PREPROCESS & PREDICT
        start = time.perf_counter()
        predicted_rul = predict_rul_from_data(df)
        elapsed = time.perf_counter() - start

        # 3. DETERMINE RISK
        risk = calculate_risk_level(predicted_rul)

        # 4. DRIFT: record the observed sensor values against the training
        # baseline. Done before metrics so the gauges reflect this request.
        drift = observe_drift(df)

        # 5. METRICS that do not depend on the report
        if PREDICTION_COUNT is not None:
            PREDICTION_COUNT.labels(risk_level=risk).inc()
            PREDICTION_LATENCY.observe(elapsed)
            PREDICTED_RUL.set(predicted_rul)
            MODEL_LOADED_GAUGE.set(1 if model_loaded else 0)
            RAG_AVAILABLE_GAUGE.set(1 if rag_agent is not None else 0)
            RAG_MODEL_MISMATCH.set(1 if rag_model_warning else 0)
            publish_drift_metrics(drift)

        # 6a. ASYNC MODE: return now, generate the report in the background.
        if async_report:
            report_id = uuid.uuid4().hex
            _REPORT_STORE[report_id] = {"status": "pending", "report": None, "source": None}
            background_tasks.add_task(_generate_report_task, report_id, predicted_rul)
            return {
                "rul": predicted_rul,
                "risk_level": risk,
                "maintenance_recommendation": "",
                "drift": drift,
                "report_id": report_id,
                "report_status": "pending",
            }

        # 6b. SYNC MODE (default): unchanged behaviour, report in the response.
        report, report_source = await get_genai_report(predicted_rul)
        if RAG_REPORT_COUNT is not None:
            RAG_REPORT_COUNT.labels(source=report_source).inc()

        return {
            "rul": predicted_rul,
            "risk_level": risk,
            "maintenance_recommendation": report,
            "drift": drift,
        }

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/reports/{report_id}", response_model=ReportResponse,
         dependencies=[Depends(require_api_key)])
@limiter.limit(READ_LIMIT)
def get_report(request: Request, report_id: str):
    """Fetch a report generated by ``/predict?async_report=true``.

    Returns 404 when the id is unknown, which includes reports evicted from the
    bounded store. The status is ``pending`` until the background task finishes.
    """
    entry = _REPORT_STORE.get(report_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown or expired report_id")
    return {
        "report_id": report_id,
        "status": entry["status"],
        "report": entry["report"],
        "source": entry["source"],
    }


def observe_drift(df: pd.DataFrame) -> dict:
    """Feed the uploaded readings into the drift monitor.

    The baseline was captured on MinMax-scaled training data, so the raw upload
    must be scaled with the same fitted scaler before comparison. Comparing raw
    sensor values (hundreds) against a scaled baseline (0-1) produces a huge
    spurious PSI for every feature.

    Returns a compact summary. Never raises: drift reporting must not break a
    prediction.
    """
    if drift_monitor is None or not drift_monitor.enabled:
        return {"enabled": False}

    if scaler is None:
        return {"enabled": True, "recorded": False, "reason": "no scaler loaded"}

    try:
        from data_preprocessing import SENSOR_COLUMNS

        numeric = df.apply(pd.to_numeric, errors="coerce")
        present = [c for c in SENSOR_COLUMNS if c in numeric.columns]

        if present:
            frame = numeric[present]
        elif numeric.shape[1] >= len(SENSOR_COLUMNS):
            # Raw C-MAPSS rows: drop id/cycle/settings, keep the 21 sensors.
            frame = numeric.iloc[:, -len(SENSOR_COLUMNS):]
            frame.columns = SENSOR_COLUMNS
        else:
            return {"enabled": True, "recorded": False, "reason": "unrecognised layout"}

        values = frame.to_numpy(dtype=np.float32)
        values = np.nan_to_num(values, nan=0.0)
        scaled = scaler.transform(values)

        # One observation per window, matching how the baseline was captured.
        # Feeding every row would compare a 50-sample slice against a
        # one-mean-per-window baseline and inflate PSI.
        window_mean = scaled.mean(axis=0)
        drift_monitor.observe(dict(zip(SENSOR_COLUMNS, window_mean, strict=True)))

        return {"enabled": True, "recorded": True, "features": drift_monitor.compute()}
    except Exception as exc:  # noqa: BLE001 - drift must never break /predict
        return {"enabled": True, "recorded": False, "error": str(exc)}


def publish_drift_metrics(drift: dict) -> None:
    """Push the drift summary onto the Prometheus gauges."""
    if DRIFT_MAX_PSI_GAUGE is None or not drift.get("features"):
        return
    for feature, stats in drift["features"].items():
        DRIFT_PSI_GAUGE.labels(feature=feature).set(stats["psi"])
    DRIFT_MAX_PSI_GAUGE.set(max(s["psi"] for s in drift["features"].values()))


@app.get("/drift", dependencies=[Depends(require_api_key)])
def drift_report():
    """Per-feature PSI against the training baseline.

    Unlike /predict this can be polled on its own, so a dashboard or alert rule
    does not need to send a prediction to read the current drift state.
    """
    if drift_monitor is None or not drift_monitor.enabled:
        return {
            "enabled": False,
            "detail": "No drift baseline loaded. Set DRIFT_BASELINE_PATH to enable.",
        }

    features = drift_monitor.compute()
    if not features:
        return {
            "enabled": True,
            "status": "insufficient_data",
            "detail": "Fewer than the minimum samples observed so far.",
            "features": {},
        }

    worst = max(features.items(), key=lambda kv: kv[1]["psi"])
    return {
        "enabled": True,
        "status": worst[1]["status"],
        "max_psi": worst[1]["psi"],
        "worst_feature": worst[0],
        "features": features,
    }


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    PREDICTION_COUNT.labels(risk_level="ANY").inc(0)  # ensure series registered
    RAG_REPORT_COUNT.labels(source="template").inc(0)
    if RAG_MODEL_MISMATCH is not None:
        RAG_MODEL_MISMATCH.set(1 if rag_model_warning else 0)
    if DRIFT_MAX_PSI_GAUGE is not None:
        DRIFT_MAX_PSI_GAUGE.set(0.0)  # ensure the series exists before traffic
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/admin/reload-model", dependencies=[Depends(require_api_key)])
def reload_model():
    """Re-read the model checkpoint from disk without restarting the API.

    The retraining DAG writes a new checkpoint to the shared model volume, then
    calls this endpoint so the fresh model is served immediately. Without it the
    container would keep the weights it loaded at startup until someone
    restarted it, which makes the "continuous learning" loop only half real.

    Disabled unless ENABLE_MODEL_RELOAD=true: swapping model weights is a
    privileged operation and the endpoint should not be reachable by default.
    """
    if os.getenv("ENABLE_MODEL_RELOAD", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(
            status_code=403,
            detail="Model reload is disabled. Set ENABLE_MODEL_RELOAD=true to enable it.",
        )

    model_path = os.getenv("MODEL_PATH", "models/lstm_best.pt")
    mtime_before = _file_mtime(model_path)

    if not load_model():
        raise HTTPException(status_code=500, detail="Reload failed: model could not be loaded")

    if MODEL_LOADED_GAUGE is not None:
        MODEL_LOADED_GAUGE.set(1)

    return {
        "status": "reloaded",
        "model_loaded": model_loaded,
        "model_path": model_path,
        "mtime_before": mtime_before,
        "mtime_after": _file_mtime(model_path),
    }


def _file_mtime(path: str):
    """Return the checkpoint mtime as an ISO string, or None if absent."""
    import datetime

    try:
        return datetime.datetime.fromtimestamp(
            os.path.getmtime(path), tz=datetime.timezone.utc
        ).isoformat()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# API versioning
# ---------------------------------------------------------------------------
# Called at the *end* of the module on purpose. `install_versioning` clones the
# routes that already exist, so running it next to `install_rate_limit(app)`
# (before any @app.post/@app.get has executed) would find nothing to clone and
# silently alias zero endpoints. Keeping it last means every route defined above
# gets a /v1 alias, and the unversioned paths keep working unchanged.
install_versioning(app)
