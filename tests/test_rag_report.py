"""Tests for the RAG-backed maintenance report path in the API.

The API previously returned one of two hardcoded strings from
``get_genai_report``. It now uses the ChromaDB retrieval index, and these tests
pin the contract that matters: a report is always returned, the source is
reported honestly, and a failure in retrieval or the LLM degrades to the
template instead of raising.
"""

import pytest

from src import api


@pytest.fixture(autouse=True)
def _reset_rag_agent():
    """Keep the module-level agent from leaking between tests."""
    original = api.rag_agent
    yield
    api.rag_agent = original


class _StubAgent:
    """Minimal stand-in for MaintenanceRAG."""

    def __init__(self, report="STUB REPORT", raises=None):
        self._report = report
        self._raises = raises

    def generate_report(self, rul):
        if self._raises is not None:
            raise self._raises
        return self._report


def test_report_uses_rag_when_agent_is_available():
    api.rag_agent = _StubAgent(report="RAG-GENERATED")
    report, source = api.get_genai_report(23)
    assert report == "RAG-GENERATED"
    assert source == "rag"


def test_report_falls_back_to_template_when_agent_is_missing():
    api.rag_agent = None
    report, source = api.get_genai_report(23)
    assert source == "template"
    assert report


def test_report_falls_back_when_llm_raises():
    """An unreachable Ollama must not turn a prediction into a 500."""
    api.rag_agent = _StubAgent(raises=RuntimeError("ollama unreachable"))
    report, source = api.get_genai_report(23)
    assert source == "template"
    assert report


def test_template_report_is_risk_aware():
    """The fallback must still distinguish urgent from nominal."""
    api.rag_agent = None
    critical, _ = api.get_genai_report(10)
    nominal, _ = api.get_genai_report(200)
    assert critical != nominal
    assert "Urgency" in critical


def test_health_reports_rag_availability(monkeypatch):
    from fastapi.testclient import TestClient

    # The lifespan calls load_rag(), which would overwrite the stub, so make it
    # a no-op and assert the endpoint reflects the module state.
    monkeypatch.setattr(api, "load_rag", lambda: True)

    api.rag_agent = _StubAgent()
    with TestClient(api.app) as client:
        body = client.get("/").json()
    assert body["rag_available"] is True

    api.rag_agent = None
    with TestClient(api.app) as client:
        body = client.get("/").json()
    assert body["rag_available"] is False


def test_metrics_expose_rag_gauges():
    from fastapi.testclient import TestClient

    with TestClient(api.app) as client:
        text = client.get("/metrics").text
    assert "aeropredict_rag_available" in text
    assert "aeropredict_reports_total" in text


def test_urgency_band_boundaries():
    """The band is a deterministic lookup, so its edges must be exact."""
    from src.rag_inference import _urgency_band

    assert _urgency_band(126) == "Nominal (Low urgency)"
    assert _urgency_band(125) == "Watch (Low urgency)"
    assert _urgency_band(80) == "Watch (Low urgency)"
    assert _urgency_band(79) == "Elevated (Medium urgency)"
    assert _urgency_band(50) == "Elevated (Medium urgency)"
    assert _urgency_band(49) == "High (High urgency)"
    assert _urgency_band(25) == "High (High urgency)"
    assert _urgency_band(24) == "Critical (High urgency)"
