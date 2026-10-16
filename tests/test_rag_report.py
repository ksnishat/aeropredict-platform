"""Tests for the RAG-backed maintenance report path in the API.

The API previously returned one of two hardcoded strings from
``get_genai_report``. It now uses the ChromaDB retrieval index, and these tests
pin the contract that matters: a report is always returned, the source is
reported honestly, and a failure in retrieval or the LLM degrades to the
template instead of raising.
"""

import asyncio

import pytest

from src import api


@pytest.fixture(autouse=True)
def _reset_rag_agent():
    """Keep the module-level agent from leaking between tests."""
    original = api.rag_agent
    yield
    api.rag_agent = original


def _report(rul):
    """Run the async report generator to completion.

    ``get_genai_report`` is async because the LLM call is offloaded to a worker
    thread — calling it directly from the async handler blocked the event loop
    and serialised every request behind the slowest one.
    """
    return asyncio.run(api.get_genai_report(rul))


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
    report, source = _report(23)
    assert report == "RAG-GENERATED"
    assert source == "rag"


def test_report_falls_back_to_template_when_agent_is_missing():
    api.rag_agent = None
    report, source = _report(23)
    assert source == "template"
    assert report


def test_report_falls_back_when_llm_raises():
    """An unreachable Ollama must not turn a prediction into a 500."""
    api.rag_agent = _StubAgent(raises=RuntimeError("ollama unreachable"))
    report, source = _report(23)
    assert source == "template"
    assert report


def test_template_report_is_risk_aware():
    """The fallback must still distinguish urgent from nominal."""
    api.rag_agent = None
    critical, _ = _report(10)
    nominal, _ = _report(200)
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


class TestOllamaModelResolution:
    """A configured tag that is not pulled must not fail silently.

    The API previously used the code default "llama3.2" while only "llama3.2:1b"
    was pulled. The index loaded, /health reported rag_available: true, and every
    report degraded to the template — 35 template reports and 0 RAG reports
    before anyone noticed.
    """

    TAGS = ["llama3.2:1b", "llava:7b", "gemma:latest"]

    def test_present_tag_is_used_as_is(self):
        from src.rag_inference import resolve_llm_model

        model, warning = resolve_llm_model("llama3.2:1b", "http://x", self.TAGS)
        assert model == "llama3.2:1b"
        assert warning is None

    def test_missing_tag_falls_back_to_the_same_family(self):
        from src.rag_inference import resolve_llm_model

        model, warning = resolve_llm_model("llama3.2", "http://x", self.TAGS)
        assert model == "llama3.2:1b", "should prefer a same-family tag"
        assert warning and "llama3.2" in warning

    def test_missing_tag_with_no_family_match_uses_any_available(self):
        from src.rag_inference import resolve_llm_model

        model, warning = resolve_llm_model("nonexistent", "http://x", self.TAGS)
        assert model in self.TAGS
        assert warning is not None

    def test_unreachable_server_keeps_the_requested_tag(self):
        """Substituting on an unreachable server would mask the real error."""
        from src.rag_inference import resolve_llm_model

        model, warning = resolve_llm_model("llama3.2", "http://x", [])
        assert model == "llama3.2"
        assert warning is None

    def test_none_uses_the_default(self):
        from src.rag_inference import DEFAULT_LLM_MODEL, resolve_llm_model

        model, _ = resolve_llm_model(None, "http://x", [DEFAULT_LLM_MODEL])
        assert model == DEFAULT_LLM_MODEL

    def test_warning_names_the_available_tags(self):
        """The warning must be actionable without a second lookup."""
        from src.rag_inference import resolve_llm_model

        _, warning = resolve_llm_model("llama3.2", "http://x", self.TAGS)
        for tag in self.TAGS:
            assert tag in warning, f"{tag} missing from the warning"

    def test_health_reports_the_resolved_model(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src import api

        monkeypatch.setattr(api, "load_rag", lambda: True)
        monkeypatch.setattr(api, "rag_agent", _StubAgent())
        monkeypatch.setattr(api, "rag_model_name", "llama3.2:1b")
        monkeypatch.setattr(api, "rag_model_warning", "substituted")

        with TestClient(api.app) as client:
            body = client.get("/").json()
        assert body["rag_model"] == "llama3.2:1b"
        assert body["rag_model_warning"] == "substituted"

    def test_mismatch_gauge_is_exposed(self, monkeypatch):
        from fastapi.testclient import TestClient

        from src import api

        monkeypatch.setattr(api, "load_rag", lambda: True)
        monkeypatch.setattr(api, "rag_model_warning", "substituted")

        with TestClient(api.app) as client:
            text = client.get("/metrics").text
        assert "aeropredict_rag_model_mismatch" in text

    def test_maintenance_rag_actually_calls_the_resolver(self, monkeypatch):
        """The resolver existing is not enough; it must be wired in.

        Testing resolve_llm_model() alone passed even with the bug present,
        because the function worked — MaintenanceRAG just never called it.
        """
        from src import rag_inference

        calls = []

        def spy(requested, base_url, available=None):
            calls.append(requested)
            return "spy-model", "spy-warning"

        monkeypatch.setattr(rag_inference, "resolve_llm_model", spy)
        monkeypatch.setattr(
            rag_inference, "HuggingFaceEmbeddings", lambda **kw: object()
        )
        monkeypatch.setattr(rag_inference, "Ollama", lambda **kw: object())

        agent = rag_inference.MaintenanceRAG(llm_model="llama3.2")

        assert calls == ["llama3.2"], "MaintenanceRAG did not call resolve_llm_model"
        assert agent.llm_model_name == "spy-model"
        assert agent.model_warning == "spy-warning"
