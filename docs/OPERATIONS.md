# AeroPredict — Operations Reference

Setup, usage, configuration and troubleshooting for AeroPredict. The
[README](../README.md) covers what the project is and how it scores; this file
covers how to run and debug it.

## Usage
| Service | URL | Credentials |
|---------|-----|-------------|
| Airflow | http://localhost:8081 | `airflow / airflow` |
| Streamlit UI | http://localhost:8501 | — |
| Grafana | http://localhost:3000 | `admin / admin` |
| MLflow | http://localhost:5000 | — |
| FastAPI | http://localhost:8000 | — |
| Prometheus | http://localhost:9090 | — |

> **Running the stack:** `./run_stack.sh` starts this project's API, MLflow, MinIO, Prometheus and Grafana. Dashboard: `streamlit run src/app.py` (port 8501). Airflow and the feature store run through `docker compose`. The four-project orchestrator is kept in the author's workspace and is not part of this repository.

Typical workflow:

1. Start Airflow, unpause `aeropredict_continuous_learning`.
2. Wait for `retrain_lstm_model` and `generate_maintenance_report` to succeed.
3. Open the Streamlit UI, or POST a sensor CSV to `/predict`:

```bash
# /predict takes a multipart file upload, not a JSON body.
# A single engine's rows work best: the API takes the last 50 cycles.
awk '$1==100' Aircraft-Engine-Failure-Prediction-using-Deep-Learning-main/CMAPSSData/test_FD001.txt > /tmp/sample.csv
curl -X POST http://localhost:8000/predict -F "file=@/tmp/sample.csv"
# {"rul":20,"risk_level":"CRITICAL","maintenance_recommendation":"..."}
# Engine 100's published RUL is 20 cycles.
```

> **Short histories.** The model consumes a fixed 50-cycle window. If you upload
> fewer than 50 rows the API left-pads by repeating the first reading, so the
> prediction is biased toward a healthier engine. Engine 1 of the FD001 test set
> has only 31 observed cycles and returns 60 against a published RUL of 112 for
> exactly this reason. Upload at least 50 cycles for a trustworthy number.

## Configuration
Endpoints are configurable so the code runs outside docker-compose:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MLFLOW_TRACKING_URI` | `http://mlflow:5000` | MLflow tracking server |
| `MLFLOW_S3_ENDPOINT_URL` | `http://minio:9000` | Artifact store (MinIO) |
| `MODEL_PATH` | `models/lstm_best.pt` | TorchScript checkpoint for the API |
| `SCALER_PATH` | `models/scaler.joblib` | Feature scaler fitted during training |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Ollama model name |
| `KNOWLEDGE_BASE_PATH` | `knowledge_base/turbofan_maintenance.txt` | RAG source document |
| `CHROMA_PERSIST_DIR` | `data/chroma` | Persisted vector index location |
| `CHROMA_COLLECTION` | `turbofan_maintenance` | ChromaDB collection name |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |

## Project Structure
```plaintext
aeropredict-platform/
├── dags/
├── docs/
├── helm-chart/
├── infrastructure/
├── k8s/
├── src/
│   ├── flask_admin/
│   ├── scripts/
│   ├── services/
│   ├── utils/
│   ├── __init__.py
│   ├── api.py
│   ├── app.py
│   ├── auth.py
│   ├── config.py
│   ├── data_preprocessing.py
│   ├── drift.py
│   ├── evaluate_model.py
│   ├── rag_eval.py
│   ├── rag_inference.py
│   ├── ratelimit.py
│   ├── retry.py
│   ├── train_model.py
│   ├── uploads.py
│   └── versioning.py
├── terraform/
├── tests/
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.gpu
├── Dockerfile.serve
├── dvc.yaml
├── load_test.py
├── Makefile
├── params.yaml
├── provision_dashboards.py
├── pyproject.toml
├── README.md
├── requirements-gpu.txt
├── requirements-serve.txt
├── run_stack.sh
├── uv.lock
└── verify_stack.sh
```

## RAG Maintenance Assistant
`src/rag_inference.py` builds a **ChromaDB** vector index over
`knowledge_base/turbofan_maintenance.txt` and retrieves the passages relevant to
a specific RUL prediction before asking a local LLM to write the report.

The API loads this index at startup and uses it for the
`maintenance_recommendation` field of every `/predict` response. If the index or
the LLM is unavailable the API still returns a report, but it is the
deterministic template rather than a generated one, and
`aeropredict_reports_total{source="template"}` climbs so the degradation is
visible rather than silent.

This replaced two earlier implementations. The first read the first five pages
of a PDF and stuffed the raw text into the prompt: that was not retrieval, it
sent the same fixed context for every prediction and could not cite which part
of the reference supported a claim. The second built the index but never wired
it to the API, so `/predict` returned one of two hardcoded strings while the
README described retrieval.

### How it works
1. The reference is split with `RecursiveCharacterTextSplitter` (900 chars, 150
   overlap). Section-divider rules are stripped first, because otherwise they
   become their own chunks and dominate retrieval.
2. Chunks are embedded with `all-MiniLM-L6-v2` and persisted to ChromaDB at
   `data/chroma/` — built once, reused across runs.
3. A query built from the predicted RUL retrieves the top 4 passages.
4. The LLM answers **only** from those passages, in a fixed
   urgency / mode / action / uncertainty structure.

### Verified behaviour
With the index built (13 chunks), a query for a 23-cycle RUL retrieves the RUL
band table and the HPT degradation section — the two passages that actually
answer the question:

```
score=0.7102 :: 'below 50 cycles requires an immediate inspection. SECTION 6: DIAGNOSTIC...'
score=0.7362 :: 'the health index. A health index of 1.0 indicates a new engine; 0.0...'
score=1.0499 :: 'because it directly affects core power extraction. Observable signature:...'
score=1.0682 :: 'engine log. SECTION 7: REPORTING REQUIREMENTS...'
```

### Configuration
| Variable | Default | Purpose |
|----------|---------|---------|
| `KNOWLEDGE_BASE_PATH` | `knowledge_base/turbofan_maintenance.txt` | Document to index |
| `CHROMA_PERSIST_DIR` | `data/chroma` | Vector store location |
| `CHROMA_COLLECTION` | `turbofan_maintenance` | Collection name |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model |
| `OLLAMA_MODEL` | `llama3.2` | Generation model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |

To index your own reference instead:

```bash
KNOWLEDGE_BASE_PATH=/path/to/reference.txt python -m src.rag_inference
```

> **Hardware note:** a 4 GB GPU cannot hold a 26B model. Use a small model
> (1B-3B) for responsive generation, or accept CPU-bound latency with a larger
> one. The embedding model is small enough to run on CPU if the GPU is busy.

### Measuring retrieval quality
Retrieval that *runs* and retrieval that *finds the right passage* are different
claims, and only the second one is testable. `src/rag_eval.py` scores the
retriever against a labelled set of 10 questions. Each case names the section
that should be retrieved and evidence tokens that must appear in the retrieved
text — tokens chosen so a passage from the wrong section cannot satisfy them by
accident.

```bash
uv run --frozen python src/rag_eval.py                  # report and gate
uv run --frozen python src/rag_eval.py --json           # machine-readable
uv run --frozen python src/rag_eval.py --min-recall 0.9  # stricter gate
```

Current result: `recall@4 = 1.000`, MRR 0.900, `hit@1` 0.800 across 10 cases.

Retrieval metrics need only the embedding model and the index, so they run
offline and deterministically and are gated in CI (`recall@4 >= 0.8`).
Generation metrics are deliberately *not* gated: they need an LLM, a CI runner
has none, and a faithfulness score that silently reports 0.0 when the model is
absent is worse than no score.

Two things the first run surfaced:

- **Chunk 3 of 13 merges sections 3.3 and 3.4**, so a question about fan
  efficiency loss retrieves a passage that also describes fan *flow* loss.
  Section-aware chunking was tried as a fix and made recall **worse**
  (1.000 → 0.900) by producing smaller chunks and losing surrounding context,
  so it was reverted. The merged section is reported as a ranking weakness
  rather than traded for recall.
- **The metric itself had a bug.** The first version checked the wrong-section
  tokens across all k passages, which flags a neighbouring section that is
  harmless because the generator sees both. Only the top-ranked passage is
  checked now.

## Monitoring detail
| Metric | Type | Meaning |
|--------|------|---------|
| `aeropredict_predictions_total` | counter | Predictions served, labelled by `risk_level` |
| `aeropredict_prediction_latency_seconds` | histogram | Inference latency |
| `aeropredict_last_rul_value` | gauge | Most recent predicted RUL |
| `aeropredict_model_loaded` | gauge | 1 when the trained model is used, 0 for the fallback |
| `aeropredict_rag_available` | gauge | 1 when the ChromaDB index is ready, 0 when reports use the template |
| `aeropredict_reports_total` | counter | Reports generated, labelled by `source` (`rag` / `template`) |
| `aeropredict_feature_drift_psi` | gauge | PSI per sensor vs the training baseline |
| `aeropredict_max_drift_psi` | gauge | Largest per-sensor PSI in the current window |

## Drift detection
`GET /drift` reports the Population Stability Index between live sensor readings
and the distribution the model was trained on. The baseline is captured during
training into `models/drift_baseline.json`.

Two details matter for correctness:

* The baseline is MinMax-scaled, so the API scales uploads with the same fitted
  scaler before comparing. Comparing raw sensor values (hundreds) against a
  scaled baseline (0-1) produced a spurious PSI of 13.8 for every feature.
* A prediction is one engine's 50-cycle window, so the baseline stores one mean
  per training window. Storing every row compared a narrow slice against the
  full training mixture and inflated PSI.

Six of the 21 FD001 sensors are constant, which collapses the quantile bins, so
they are dropped from the baseline rather than reporting meaningless drift.

Verified discrimination: a representative sample of windows gives mean PSI 0.19,
all-healthy windows 2.83, all-degraded 8.33.

## Airflow pipeline
`aeropredict_continuous_learning` retrains the LSTM, hot-swaps the served model,
then generates a maintenance report (retrain → reload → report), logging training
metrics to MLflow:

```bash
airflow dags unpause aeropredict_continuous_learning
airflow dags trigger aeropredict_continuous_learning
```

The reload step calls `POST /admin/reload-model`, which is disabled unless
`ENABLE_MODEL_RELOAD=true`. Without it the retrained checkpoint would sit on disk
while the running process kept serving the weights it loaded at startup — which
is exactly what happened before: the DAG wrote to the `airflow_models` volume
while the API read `aeropredict_models`, so "continuous learning" retrained a
model that was never served.

## Troubleshooting
| Issue | Solution |
|-------|----------|
| **Ollama connection refused** | Ensure Ollama is running with `OLLAMA_HOST=0.0.0.0 ollama serve` and check firewall settings |
| **RAG report is a template fallback** | The LLM was unreachable or the index failed to build. Check `ollama list` and that `knowledge_base/turbofan_maintenance.txt` exists |
| **RAG responses are very slow** | A large Ollama model is spilling to CPU. Check `ollama ps` — if PROCESSOR shows a high CPU percentage, switch to a smaller model via `OLLAMA_MODEL` |
| **`torch.OutOfMemoryError` while embedding** | The GPU is occupied. Set `CUDA_VISIBLE_DEVICES=""` to run the embedding model on CPU |
| **MLflow UI not loading** | Verify MLflow service is healthy: `kubectl get pods` or `docker compose ps mlflow` |
| **Model not loading in API** | Check that model files exist in PVC and that MLflow tracking URI is correct |
| **High memory usage in Streamlit** | Reduce batch size in data preprocessing or increase container memory limits |
| **Ingress TLS not working** | Ensure cert-manager is installed and ClusterIssuer is configured correctly |
| **Pods crashing with OOMKill** | Increase resource limits in values.yaml or check for memory leaks in custom code |
| **Database connection failed** | Verify PostgreSQL service is running and credentials in secrets are correct |
| **Prometheus not scraping metrics** | Check that `/metrics` endpoint is exposed and ServiceMonitor is configured |
| **Grafana shows "No data"** | The provisioning or dashboards directory is not mounted, or Grafana is not on the `mlops-stack` network so it cannot resolve `prom_<project>` | Run `python3 provision_dashboards.py` — it reports which of the two it is |
| **Model returns a constant RUL** | Known limitation of the current LSTM — see [Known limitation](#known-limitation-honest-disclosure) |
| **Airflow DAG fails at `retrain_lstm_model`** | Ensure `AEROPREDICT_SRC` points at the mounted `src/` directory and that MLflow is reachable from the container |
