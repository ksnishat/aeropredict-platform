import streamlit as st
import requests
import pandas as pd
import plotly.express as px

# CONFIG
API_URL = "http://fastapi:8000/predict"  # Docker service name 'fastapi'

st.set_page_config(page_title="NASA AeroPredict", layout="wide", page_icon="✈️")

# HEADER
st.title("✈️ NASA AeroPredict: AI Technician Dashboard")
st.markdown("Real-time RUL Prediction & GenAI Diagnostics")
st.markdown("---")

# SIDEBAR
st.sidebar.header("🔧 Configuration")
uploaded_file = st.sidebar.file_uploader("Upload Engine Telemetry (txt/csv)", type=["txt", "csv"])

if uploaded_file:
    # 1. SHOW DATA
    st.subheader(" Live Sensor Telemetry")
    # Reset pointer to read for display
    df = pd.read_csv(uploaded_file, sep='\s+', header=None)
    st.dataframe(df.head(5), use_container_width=True)
    
    # 2. CALL API
    if st.sidebar.button(" Run Diagnostics"):
        with st.spinner("Analyzing Sensor Patterns..."):
            try:
                # Reset file pointer for API upload
                uploaded_file.seek(0)
                files = {"file": uploaded_file}
                
                # POST REQUEST
                response = requests.post(API_URL, files=files)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # 3. DISPLAY RESULTS
                    st.markdown("###  Diagnostic Results")
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.metric("Predicted RUL", f"{result['rul']} Cycles")
                    with col2:
                        color = "red" if result['risk_level'] == "CRITICAL" else "green"
                        st.markdown(f"**Risk Level:** :{color}[{result['risk_level']}]")
                    with col3:
                         st.info(f"**AI Rec:** {result['maintenance_recommendation']}")
                    
                    # 4. CHART
                    st.subheader(" Sensor Degradation Trend (Sensor #11)")
                    st.line_chart(df.iloc[:, 11])  # Plotting specific sensor column
                    
                else:
                    st.error(f"API Error: {response.text}")
                    
            except Exception as e:
                st.error(f"Connection Failed: {e}")
                st.info("Ensure Docker is running and the FastAPI service is up.")

else:
    st.info(" Please upload a NASA C-MAPSS data file (e.g., test_FD001.txt) to begin.")