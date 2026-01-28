# AeroPredict: GenAI-Powered Predictive Maintenance Platform

**AeroPredict** is a production-grade MLOps and Generative AI platform designed to predict the Remaining Useful Life (RUL) of aircraft turbofan engines. It simulates a modern industrial AI system by integrating deep learning for time-series forecasting with a RAG-powered LLM agent that generates automated, physics-informed maintenance reports.

The system is built on the NASA C-MAPSS dataset and orchestrates the entire lifecycle—from data ingestion to technician reporting—using Apache Airflow, MLflow, and Docker.

## Key Features

- **Deep Learning Forecasting:** A custom LSTM (Long Short-Term Memory) network trained on multivariate sensor trajectories to predict RUL with an asymmetric safety-first loss function.

- **GenAI Diagnostics (RAG):** A local Llama 3.2 agent (via Ollama) indexes technical manuals to explain failure modes (e.g., "HPC Efficiency Loss") based on real-time telemetry.

- **Automated Pipelines:** Apache Airflow DAGs manage the end-to-end workflow: Ingestion → Preprocessing → Training → Evaluation → Deployment.

- **Technician Dashboard:** A Streamlit interface connected to a FastAPI backend allows engineers to upload sensor logs and view instant predictions and AI-generated repair advice.

- **Full Observability:** Prometheus and Grafana monitor system health, container metrics, and inference latency in real-time.

## Architecture

The system follows a microservices architecture orchestrated by Docker Compose:

| Service | Technology | Role |
|---------|-----------|------|
| **Orchestrator** | Apache Airflow | Manages DAGs and triggers pipeline tasks. |
| **Model Training** | PyTorch + MLflow | Trains the LSTM and tracks experiments/artifacts. |
| **GenAI Engine** | Ollama (Llama 3.2) | Serves the Large Language Model for RAG. |
| **Backend API** | FastAPI | Exposes model inference and RAG logic via REST endpoints. |
| **Frontend UI** | Streamlit | Provides the interactive dashboard for end-users. |
| **Monitoring** | Grafana + Prometheus | Visualizes hardware usage and system health. |

***

## Diagnostic Methodology

AeroPredict combines data-driven RUL modeling with a physics-informed health index and safety-oriented scoring.

### 1. Asymmetric RUL Scoring

- The system uses an **asymmetric scoring function** where **late predictions** (predicting failure *after* it occurs) incur significantly higher penalties than **early predictions**.
- This aligns optimization with **flight safety**, pushing the LSTM to err on the safe side when uncertainty is high.

### 2. Health Index \(h(t)\)

- A **Health Index** \(h(t)\) is defined as the **minimum of operative margins** across key subsystems:
  - Fan
  - High-Pressure Compressor (HPC)
  - High-Pressure Turbine (HPT)
  - Exhaust Gas Temperature (EGT)
- Intuitively, the engine health is limited by the **worst-performing margin**, so taking the minimum captures the most critical bottleneck at each time step.

### 3. Failure Criterion and RAG Trigger

- When \(h(t)\) crosses a configured threshold (e.g., reaches **zero** in normalized units), the system declares that a **failure criterion** is met.
- At this point:
  - The last RUL prediction and trajectory context are frozen.
  - The RAG pipeline queries the *Damage Propagation Modeling* manual for matching degradation patterns.
  - Llama 3.2 generates a final **maintenance and root-cause report** (e.g., "efficiency loss in HPC with associated EGT drift").

***

## Step-by-Step Setup Guide

### 1. Environment Preparation

Ensure the following are installed on your host machine:

- Docker & Docker Compose
- Python 3.10 (for local development)
- **Ollama** (for hosting Llama 3.2 on the host, reachable from Docker)

Create the base project structure:

```bash
# Create project structure
mkdir -p data/raw data/processed logs plugins tests
```

### 2. Dataset Acquisition

After downloading the NASA C-MAPSS files and the manual, move them to `data/raw` so that Airflow and the ML pipeline can access them via the shared volume.

```bash
# Move raw C-MAPSS text files so the pipeline can access them
mv data/raw/*.txt data/
```

(If starting fresh, follow the directory setup steps in the Data Management section.)

### 3. Setup the GenAI “Brain” (Ollama)

The GenAI module requires **Llama 3.2** running from the host, reachable at `OLLAMA_HOST=0.0.0.0` so containers can connect.

```bash
# Pull the required model
OLLAMA_HOST=0.0.0.0 ollama pull llama3.2

# Start the server with public access for Docker containers
OLLAMA_HOST=0.0.0.0 ollama serve
```

### 4. Build and Launch the Platform

Use Docker Compose to build the custom images (Airflow, API, UI, monitoring stack) and start everything in detached mode.

```bash
# Build custom images and start the microservices
docker compose up --build -d
```

Once containers are healthy, access services using the URLs below.

***

## Usage & Credentials

| Service     | URL                    | Credentials (User / Pass) |
|-------------|------------------------|----------------------------|
| **Airflow** | http://localhost:8080  | `airflow / airflow` |
| **Streamlit UI** | http://localhost:8501 | N/A (public) |
| **Grafana** | http://localhost:3000  | `airflow / airflow` |
| **MLflow**  | http://localhost:5000  | N/A (public) |
| **FastAPI** | http://localhost:8000  | N/A (OpenAPI docs at `/docs`) |

Typical workflow:

- Start Airflow, unpause the main DAG (e.g., `aeropredict_pipeline`).  
- Wait for preprocessing and training runs to complete.  
- Open the Streamlit UI, select an engine or upload a test trajectory, view predicted RUL and generated maintenance report.

***

## Running Tests

Unit tests validate data preprocessing assumptions, RUL label generation, and API contracts.

Run tests from the API container:

```bash
# Run tests inside the API container
docker exec -it aeropredict_api python -m unittest discover tests/
```

Add more tests under `tests/` for new models, scoring variants, or endpoints as the project evolves.

***

## Project Structure

```plaintext
aeropredict-platform/
├── dags/                       # Airflow DAG definitions
├── src/                        # Main application code
│   ├── api.py                  # FastAPI backend (RUL + diagnostics API)
│   ├── app.py                  # Streamlit technician dashboard
│   ├── train_model.py          # LSTM training logic
│   ├── rag_inference.py        # GenAI RAG implementation
│   └── data_preprocessing.py   # Data cleaning, normalization, and sequencing
├── infrastructure/             # DevOps & monitoring
│   ├── docker/                 # Custom Dockerfiles
│   └── monitoring/             # Prometheus/Grafana configs
├── data/                       # C-MAPSS datasets & manuals (mounted into Airflow)
│   ├── raw/                    # Original text files and PDFs
│   └── processed/              # Normalized and windowed tensors
├── tests/                      # Unit tests
└── docker-compose.yml          # Infrastructure orchestration
```



***

## Monitoring & Observability

- **Prometheus** scrapes metrics from the API, Airflow, and system exporters (e.g., Node Exporter).
- **Grafana** dashboards track:
  - RUL inference latency and throughput.
  - Airflow task duration and failure rates.
  - Container CPU, memory, and GPU utilization where applicable.

Monitoring helps detect data drift (e.g., abnormal sensor distributions) and infrastructure bottlenecks before they impact production performance.

***

## Author

Developed by **Khaled Saifullah**.

For collaboration, feature requests, or bug reports, please open an issue or contact the maintainer via the repository issue tracker.