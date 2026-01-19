# ✈️ AeroPredict: GenAI-Powered Predictive Maintenance Platform

**AeroPredict** is an end-to-end MLOps and **GenAI** platform for predicting the **Remaining Useful Life (RUL)** of aircraft turbofan engines using the NASA **C-MAPSS** dataset. It combines a **time-series LSTM model** for RUL forecasting with a **RAG-powered Llama 3.2 agent** that generates technical maintenance reports grounded in the *Damage Propagation Modeling* manual.

***

## 🚀 Key Features

- **🔮 RUL Forecasting Engine:** Sequence-to-one **LSTM** model trained on NASA C-MAPSS turbofan multivariate time-series to predict remaining flight cycles until failure.
- **🧠 GenAI Diagnostics (RAG):** Local **Llama 3.2** (via Ollama) reads the *Damage Propagation Modeling* PDF to identify degradation modes such as *Efficiency Loss* and *Flow Loss* and explain failure mechanisms.
- **📡 End-to-End Orchestration:** **Apache Airflow** DAGs manage ingestion, preprocessing, training, evaluation, model registration, and report generation with full reproducibility.
- **📊 Monitoring & Observability:** **Prometheus** and **Grafana** track container and hardware metrics to ensure stable long-running training and inference.
- **🖥️ Technician Dashboard:** **Streamlit** frontend connected to a **FastAPI** backend for real-time RUL queries, health index visualization, and GenAI maintenance reports.

***

## 🛠️ Tech Stack

- **Domain / ML**
  - PyTorch (**LSTM** RUL model)
  - Scikit-Learn (metrics, preprocessing utilities)
  - Pandas / NumPy (time-series wrangling)

- **Orchestration & MLOps**
  - Apache Airflow 2.7.1 (pipelines)
  - MLflow (experiment tracking and model registry)
  - MinIO (S3-compatible artifact store for models and processed datasets)

- **Generative AI**
  - Llama 3.2 (via **Ollama** on the host)
  - RAG pipeline using `pypdf` to index the NASA technical manual

- **Backend & Frontend**
  - FastAPI + Uvicorn (REST API for prediction and diagnostics)
  - Streamlit + Plotly (technician UI and plots)

- **Monitoring & DevOps**
  - Docker & Docker Compose (multi-service deployment)
  - Prometheus, Grafana, Node Exporter (metrics and dashboards)

***

## 📊 Data Management & NASA C-MAPSS

The platform is built around the **NASA C-MAPSS (Commercial Modular Aero-Propulsion System Simulation)** dataset, which provides multivariate sensor trajectories of turbofan engines operated under varying conditions until failure.

### 1. Dataset Source

- **Dataset:** NASA C-MAPSS (FD001 subset)  
- **Download:** Kaggle – *NASA CMAPSS Jet Engine Data* (FD001)  
- **Required Files (FD001):**
  - `train_FD001.txt` – training trajectories
  - `test_FD001.txt` – testing trajectories
  - `RUL_FD001.txt` – ground-truth RUL labels for test trajectories

Place these files in the local `data/raw` folder (see directory layout below).

### 2. Manuals for RAG

The GenAI diagnostic agent requires the official NASA technical documentation:

- **File:** `Damage_Propagation_Modeling.pdf`  
- **Location:** `data/raw/Damage_Propagation_Modeling.pdf`

This manual is parsed and indexed using a RAG pipeline so that Llama 3.2 can justify predicted failure modes using grounded technical language.

### 3. Directory Setup Guide

The Docker containers mount your local `data/` directory into the Airflow container at `/opt/airflow/data`. The directory hierarchy must be:

```bash
# 1. Create the local data hierarchy
mkdir -p data/raw data/processed

# 2. Copy C-MAPSS files into the raw folder
cp ~/Downloads/nasa-cmaps/*.txt ./data/raw/

# 3. Place the NASA Damage Propagation manual for the RAG agent
cp ~/Downloads/Damage_Propagation_Modeling.pdf ./data/raw/
```

- `data/raw/` – original C-MAPSS text files and manual.  
- `data/processed/` – normalized, windowed tensors prepared for training.

### 4. Data Processing Flow

- **Ingestion:** Airflow sensors watch `data/raw` and trigger the pipeline once the required files are present.
- **Normalization:** `data_preprocessing.py` applies Min–Max scaling across the **21 sensor channels** and relevant operational settings.
- **Windowing:** Time-series trajectories are converted into fixed-length sliding windows suitable for LSTM input (e.g., $T$-step sequences with a single RUL target).
- **Artifact Store:** Processed numpy/torch arrays and trained model checkpoints are persisted in **MinIO** and registered in **MLflow**.

***

## 🏗️ Architecture

AeroPredict uses a modular microservices architecture orchestrated by **Apache Airflow** and deployed via **Docker Compose**.

### High-Level Workflow

| Stage            | Description |
|------------------|-------------|
| **Ingestion**    | Airflow DAG watches `data/raw` for C-MAPSS text files and the NASA manual, then triggers preprocessing tasks. |
| **Preprocessing**| `data_preprocessing.py` normalizes sensor channels, builds sliding windows, and splits train/validation sets. |
| **Training**     | `train_model.py` trains an LSTM model to predict RUL cycles, logging metrics, parameters, and artifacts to MLflow / MinIO. |
| **Inference**    | A **FastAPI** service loads the best model checkpoint and exposes `/predict` and `/diagnostics` endpoints. |
| **GenAI (RAG)**  | If predicted RUL crosses a critical threshold, **Llama 3.2** is invoked to interpret degradation modes from the manual and generate a textual report. |
| **Dashboard**    | **Streamlit** consumes the FastAPI endpoints, visualizes health index and RUL, and renders GenAI maintenance reports to technicians. |
| **Monitoring**   | **Prometheus** scrapes metrics, **Grafana** dashboards display container health, GPU/CPU usage, and request latencies. |

### Core Components

- **Orchestration – Airflow**
  - DAGs for ingestion → preprocessing → training → evaluation → deployment → reporting.  
  - Volume mapping: host `data/` → `/opt/airflow/data`.

- **Model Training – LSTM**
  - PyTorch LSTM network on sensor response surfaces to forecast remaining cycles.  
  - Custom loss implementing an **asymmetric scoring function** that penalizes **late predictions more than early ones** to prioritize safety.

- **GenAI Diagnostics – RAG**
  - `rag_inference.py` builds a document index over the *Damage Propagation Modeling* PDF using `pypdf`.  
  - When RUL is **critical**, the pipeline extracts sections related to modes like **Efficiency Loss** and **Flow Loss** and feeds them as context to Llama 3.2 to generate an engineering-style diagnostic report.

- **Inference – FastAPI**
  - `api.py` exposes endpoints for:
    - RUL prediction for a given engine trajectory.
    - Health index computation and visualization metadata.
    - RAG-based text report generation when requested by the dashboard.

- **Dashboard – Streamlit**
  - `app.py` provides:
    - File upload or engine selection from test set.
    - RUL trend plots, health index curves, and failure threshold markers.
    - Embedded GenAI report viewer for technician decisions.

***

## 🧠 Diagnostic Methodology

AeroPredict combines data-driven RUL modeling with a physics-informed health index and safety-oriented scoring.

### 1. Asymmetric RUL Scoring

- The system uses an **asymmetric scoring function** where **late predictions** (predicting failure *after* it occurs) incur significantly higher penalties than **early predictions**.
- This aligns optimization with **flight safety**, pushing the LSTM to err on the safe side when uncertainty is high.

### 2. Health Index $h(t)$

- A **Health Index** $h(t)$ is defined as the **minimum of operative margins** across key subsystems:
  - Fan
  - High-Pressure Compressor (HPC)
  - High-Pressure Turbine (HPT)
  - Exhaust Gas Temperature (EGT)
- Intuitively, the engine health is limited by the **worst-performing margin**, so taking the minimum captures the most critical bottleneck at each time step.

### 3. Failure Criterion and RAG Trigger

- When $h(t)$ crosses a configured threshold (e.g., reaches **zero** in normalized units), the system declares that a **failure criterion** is met.
- At this point:
  - The last RUL prediction and trajectory context are frozen.
  - The RAG pipeline queries the *Damage Propagation Modeling* manual for matching degradation patterns.
  - Llama 3.2 generates a final **maintenance and root-cause report** (e.g., "efficiency loss in HPC with associated EGT drift").

***

## ⚡ Step-by-Step Setup Guide

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

### 3. Setup the GenAI "Brain" (Ollama)

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

## 🖥️ Usage & Credentials

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

## 🧪 Running Tests

Unit tests validate data preprocessing assumptions, RUL label generation, and API contracts.

Run tests from the API container:

```bash
# Run tests inside the API container
docker exec -it aeropredict_api python -m unittest discover tests/
```

Add more tests under `tests/` for new models, scoring variants, or endpoints as the project evolves.

***

## 📂 Project Structure

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

## 📈 Monitoring & Observability

- **Prometheus** scrapes metrics from the API, Airflow, and system exporters (e.g., Node Exporter).
- **Grafana** dashboards track:
  - RUL inference latency and throughput.
  - Airflow task duration and failure rates.
  - Container CPU, memory, and GPU utilization where applicable.

Monitoring helps detect data drift (e.g., abnormal sensor distributions) and infrastructure bottlenecks before they impact production performance.

***

## 📧 Author

Developed by **Khaled Saifullah**.

For collaboration, feature requests, or bug reports, please open an issue or contact the maintainer via the repository issue tracker.
