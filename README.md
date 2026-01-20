# ✈️ AeroPredict: GenAI-Powered Predictive Maintenance Platform

**AeroPredict** is a comprehensive MLOps and GenAI platform designed to predict the Remaining Useful Life (RUL) of turbofan engines using the NASA C-MAPSS dataset. It integrates a deep learning LSTM model for time-series forecasting with a RAG-powered Llama 3.2 agent that provides expert-level maintenance diagnostics grounded in technical NASA manuals.

## 🚀 Key Features

- **🔮 RUL Forecasting Engine:** Utilizes a sequence-to-one LSTM architecture to process multivariate time-series sensor data and forecast engine health.

- **🧠 GenAI Diagnostics (RAG):** A localized Llama 3.2 agent reads the Damage Propagation Modeling manual to identify degradation modes like Efficiency Loss and Flow Loss.

- **📡 End-to-End Orchestration:** Managed by Apache Airflow, the pipeline automates ingestion, preprocessing, training, and report generation.

- **📊 Observability Stack:** Prometheus and Grafana provide real-time monitoring of container health and hardware utilization.

- **🖥️ Technician Dashboard:** A Streamlit UI connected via FastAPI enables real-time queries, health index visualization, and GenAI report rendering.

## 📊 Data Acquisition & Management

### 1. Dataset Source

The platform is optimized for the FD001 subset of the NASA C-MAPSS dataset.

- **Download:** [NASA CMAPSS Jet Engine Data (Kaggle)](https://www.kaggle.com/datasets/behrad3d/nasa-cmaps-sensor-data)
- **Required Training Files:** `train_FD001.txt`, `test_FD001.txt`, and `RUL_FD001.txt`
- **GenAI Reference:** `Damage_Propagation_Modeling.pdf` (Technical Manual for RAG)

### 2. Setup Guide

The Docker containers map your local `data/` directory to `/opt/airflow/data`. Follow this directory structure:

```bash
# Create local hierarchy
mkdir -p data/raw data/processed

# Move C-MAPSS files and NASA manual into raw folder
mv ~/Downloads/*.txt ./data/raw/
mv ~/Downloads/Damage_Propagation_Modeling.pdf ./data/raw/
```

***

## 🏗️ Architecture

AeroPredict uses a modular microservices architecture orchestrated by **Apache Airflow** and deployed via **Docker Compose**. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

### High-Level Workflow

| Stage            | Description |
|------------------|-------------|
| **Ingestion**    | Airflow DAG watches `data/raw` for C-MAPSS text files and the NASA manual, then triggers preprocessing tasks.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Preprocessing**| `data_preprocessing.py` normalizes sensor channels, builds sliding windows, and splits train/validation sets.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Training**     | `train_model.py` trains an LSTM model to predict RUL cycles, logging metrics, parameters, and artifacts to MLflow / MinIO.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Inference**    | A **FastAPI** service loads the best model checkpoint and exposes `/predict` and `/diagnostics` endpoints.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **GenAI (RAG)**  | If predicted RUL crosses a critical threshold, **Llama 3.2** is invoked to interpret degradation modes from the manual and generate a textual report.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Dashboard**    | **Streamlit** consumes the FastAPI endpoints, visualizes health index and RUL, and renders GenAI maintenance reports to technicians.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Monitoring**   | **Prometheus** scrapes metrics, **Grafana** dashboards display container health, GPU/CPU usage, and request latencies.  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |

### Core Components

- **Orchestration – Airflow**
  - DAGs for ingestion → preprocessing → training → evaluation → deployment → reporting.  
  - Volume mapping: host `data/` → `/opt/airflow/data`. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

- **Model Training – LSTM**
  - PyTorch LSTM network on sensor response surfaces to forecast remaining cycles.  
  - Custom loss implementing an **asymmetric scoring function** that penalizes **late predictions more than early ones** to prioritize safety. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

- **GenAI Diagnostics – RAG**
  - `rag_inference.py` builds a document index over the *Damage Propagation Modeling* PDF using `pypdf`.  
  - When RUL is **critical**, the pipeline extracts sections related to modes like **Efficiency Loss** and **Flow Loss** and feeds them as context to Llama 3.2 to generate an engineering-style diagnostic report. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

- **Inference – FastAPI**
  - `api.py` exposes endpoints for:
    - RUL prediction for a given engine trajectory.
    - Health index computation and visualization metadata.
    - RAG-based text report generation when requested by the dashboard. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

- **Dashboard – Streamlit**
  - `app.py` provides:
    - File upload or engine selection from test set.
    - RUL trend plots, health index curves, and failure threshold markers.
    - Embedded GenAI report viewer for technician decisions. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

***

## 🧠 Diagnostic Methodology

AeroPredict combines data-driven RUL modeling with a physics-informed health index and safety-oriented scoring. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

### 1. Asymmetric RUL Scoring

- The system uses an **asymmetric scoring function** where **late predictions** (predicting failure *after* it occurs) incur significantly higher penalties than **early predictions**. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)
- This aligns optimization with **flight safety**, pushing the LSTM to err on the safe side when uncertainty is high. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

### 2. Health Index \(h(t)\)

- A **Health Index** \(h(t)\) is defined as the **minimum of operative margins** across key subsystems:
  - Fan
  - High-Pressure Compressor (HPC)
  - High-Pressure Turbine (HPT)
  - Exhaust Gas Temperature (EGT) [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)
- Intuitively, the engine health is limited by the **worst-performing margin**, so taking the minimum captures the most critical bottleneck at each time step. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

### 3. Failure Criterion and RAG Trigger

- When \(h(t)\) crosses a configured threshold (e.g., reaches **zero** in normalized units), the system declares that a **failure criterion** is met. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)
- At this point:
  - The last RUL prediction and trajectory context are frozen.
  - The RAG pipeline queries the *Damage Propagation Modeling* manual for matching degradation patterns.
  - Llama 3.2 generates a final **maintenance and root-cause report** (e.g., “efficiency loss in HPC with associated EGT drift”). [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

***

## ⚡ Step-by-Step Setup Guide

### 1. Environment Preparation

Ensure the following are installed on your host machine:

- Docker & Docker Compose
- Python 3.10 (for local development)
- **Ollama** (for hosting Llama 3.2 on the host, reachable from Docker) [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

Create the base project structure:

```bash
# Create project structure
mkdir -p data/raw data/processed logs plugins tests
```

### 2. Dataset Acquisition

After downloading the NASA C-MAPSS files and the manual, move them to `data/raw` so that Airflow and the ML pipeline can access them via the shared volume. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

```bash
# Move raw C-MAPSS text files so the pipeline can access them
mv data/raw/*.txt data/
```

(If starting fresh, follow the directory setup steps in the Data Management section.) [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

### 3. Setup the GenAI “Brain” (Ollama)

The GenAI module requires **Llama 3.2** running from the host, reachable at `OLLAMA_HOST=0.0.0.0` so containers can connect. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

```bash
# Pull the required model
OLLAMA_HOST=0.0.0.0 ollama pull llama3.2

# Start the server with public access for Docker containers
OLLAMA_HOST=0.0.0.0 ollama serve
```

### 4. Build and Launch the Platform

Use Docker Compose to build the custom images (Airflow, API, UI, monitoring stack) and start everything in detached mode. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

```bash
# Build custom images and start the microservices
docker compose up --build -d
```

Once containers are healthy, access services using the URLs below. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

***

## 🖥️ Usage & Credentials

| Service     | URL                    | Credentials (User / Pass) |
|-------------|------------------------|----------------------------|
| **Airflow** | http://localhost:8080  | `airflow / airflow`  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Streamlit UI** | http://localhost:8501 | N/A (public)  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **Grafana** | http://localhost:3000  | `airflow / airflow`  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **MLflow**  | http://localhost:5000  | N/A (public)  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |
| **FastAPI** | http://localhost:8000  | N/A (OpenAPI docs at `/docs`)  [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md) |

Typical workflow:

- Start Airflow, unpause the main DAG (e.g., `aeropredict_pipeline`).  
- Wait for preprocessing and training runs to complete.  
- Open the Streamlit UI, select an engine or upload a test trajectory, view predicted RUL and generated maintenance report. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

***

## 🧪 Running Tests

Unit tests validate data preprocessing assumptions, RUL label generation, and API contracts. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

Run tests from the API container:

```bash
# Run tests inside the API container
docker exec -it aeropredict_api python -m unittest discover tests/
```

Add more tests under `tests/` for new models, scoring variants, or endpoints as the project evolves. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

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

- **Prometheus** scrapes metrics from the API, Airflow, and system exporters (e.g., Node Exporter). [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)
- **Grafana** dashboards track:
  - RUL inference latency and throughput.
  - Airflow task duration and failure rates.
  - Container CPU, memory, and GPU utilization where applicable. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

Monitoring helps detect data drift (e.g., abnormal sensor distributions) and infrastructure bottlenecks before they impact production performance. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

***

## 📧 Author

Developed by **Khaled Saifullah**. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)

For collaboration, feature requests, or bug reports, please open an issue or contact the maintainer via the repository issue tracker. [ppl-ai-file-upload.s3.amazonaws](https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/attachments/28838993/73954e86-4b58-4282-b329-9da4280baf18/README.md)