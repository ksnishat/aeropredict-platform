# FILE: src/rag_inference.py
import requests
import os
from pypdf import PdfReader

def generate_maintenance_report(rul_prediction):
    print(f"🤖 GenAI Technician activating for RUL: {rul_prediction}...")
    
    # 1. DEFINE PATHS (PDF is in data/raw based on your tree)
    # Airflow sees it at /opt/airflow/data/raw/Damage Propagation Modeling.pdf
    pdf_path = "/opt/airflow/data/raw/Damage Propagation Modeling.pdf"
    
    # 2. EXTRACT KNOWLEDGE FROM PDF
    knowledge_base = ""
    try:
        print(f" Reading Real NASA Manual: {pdf_path}")
        reader = PdfReader(pdf_path)
        # We only read the first 5 pages to keep context small/fast for Llama3
        for i, page in enumerate(reader.pages[:5]): 
            text = page.extract_text()
            if text:
                knowledge_base += text + "\n"
        print(f" Extracted {len(knowledge_base)} characters of technical data.")
    except Exception as e:
        print(f" PDF Read Error: {e}. Falling back to basic context.")
        knowledge_base = "Standard operating limits for C-MAPSS Turbofan Engine."

    # 3. CONSTRUCT PROMPT
    prompt = f"""
    You are a Senior NASA Propulsion Engineer.
    
    === TECHNICAL REFERENCE (NASA C-MAPSS) ===
    {knowledge_base[:4000]}  # Truncate to fit context window
    === END REFERENCE ===
    
    SITUATION:
    Analysis of Engine #101 shows a Remaining Useful Life (RUL) of {rul_prediction} cycles.
    
    TASK:
    1. Cite the specific degradation mode likely occurring (Reference "Flow Loss" or "Efficiency Loss" from the text).
    2. Recommend diagnostics based on the "HPC" or "Fan" modules mentioned in the text.
    3. Determine urgency (High/Low).
    """
    
    # 4. SEND TO OLLAMA
    url = "http://host.docker.internal:11434/api/generate"
    payload = {
        "model": "llama3.2", 
        "prompt": prompt,
        "stream": False
    }
    
    try:
        print(" Analyzing PDF data with Ollama...")
        response = requests.post(url, json=payload)
        report = response.json()['response']
        
        print("\n NASA TECHNICAL REPORT:\n")
        print("="*60)
        print(report)
        print("="*60)
        return report
    except Exception as e:
        print(f" Inference Failed: {e}")
        return None

if __name__ == "__main__":
    generate_maintenance_report(23)