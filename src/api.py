from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import pandas as pd
import numpy as np
import io
import requests

# Initialize API
app = FastAPI(title="AeroPredict API", version="1.0")

# Input Schema
class PredictionResponse(BaseModel):
    rul: int
    risk_level: str
    maintenance_recommendation: str

def get_genai_report(rul):
    """Internal helper to call Ollama (same logic as RAG script)"""
    # Simplified logic for the API demo
    if rul < 50:
        return "High Urgency: Efficiency Loss detected in HPC module."
    return "Normal Operation: Standard maintenance recommended."

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "aeropredict-api"}

@app.post("/predict", response_model=PredictionResponse)
async def predict_rul(file: UploadFile = File(...)):
    try:
        # 1. READ DATA
        contents = await file.read()
        df = pd.read_csv(io.BytesIO(contents), sep=r'\s+', header=None)
        
        # 2. PREPROCESS (Simplified for demo - normally you'd scale this)
        # We take the last sequence of data to predict the NEXT step
        # For this demo, we mock the model inference to guarantee stability
        # (In prod, you would load: model = mlflow.pytorch.load_model(...))
        
        # Simulating model output based on sensor noise
        simulated_rul = int(np.random.randint(20, 150)) 
        
        # 3. DETERMINE RISK
        risk = "LOW"
        if simulated_rul < 50:
            risk = "CRITICAL"
        elif simulated_rul < 100:
            risk = "WARNING"
            
        # 4. GENAI REPORT
        report = get_genai_report(simulated_rul)

        return {
            "rul": simulated_rul,
            "risk_level": risk,
            "maintenance_recommendation": report
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))