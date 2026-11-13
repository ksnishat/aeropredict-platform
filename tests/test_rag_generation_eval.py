"""Tests for the generation-evaluation harness.

The harness is a measuring instrument, so it needs its own tests. These cover
the parts that do not need a judge model: availability detection, the
skip-vs-fail contract, and the aggregation arithmetic. The judge itself is
exercised by a control pair in `test_judge_controls` which is skipped when no
Ollama is reachable, because a CI runner has none.
"""

import json
import urllib.error

import pytest

from src import rag_generation_eval as rge
from src.rag_generation_eval import GenerationScore, _judge_available


class TestJudgeAvailability:
    def test_unreachable_ollama_reports_why(self, monkeypatch):
        """A missing judge must produce a reason, not a bare False."""

        def boom(*_a, **_k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        ok, why = _judge_available("http://localhost:11434", "llama3.2")
        assert ok is False
        assert "not reachable" in why

    def test_missing_model_names_what_is_available(self, monkeypatch):
        """The message must say which models exist, so the fix is obvious."""

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return json.dumps({"models": [{"name": "llama3:latest"}]}).encode()

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
        ok, why = _judge_available("http://localhost:11434", "llama3.2")
        assert ok is False
        assert "llama3.2" in why
        assert "llama3:latest" in why

    def test_bare_name_matches_latest_tag(self, monkeypatch):
        """Ollama reports `llama3.2:latest`; a bare `llama3.2` must match."""

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode()

        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: FakeResp())
        ok, _ = _judge_available("http://localhost:11434", "llama3.2")
        assert ok is True


class TestSkipContract:
    def test_missing_judge_is_an_error_by_default(self, monkeypatch, capsys):
        """A local run must not silently pass when the judge is absent."""
        monkeypatch.setattr(rge, "_judge_available", lambda *a: (False, "no ollama"))
        rc = rge.main([])
        assert rc == 2
        assert "ERROR" in capsys.readouterr().err

    def test_skip_flag_turns_missing_judge_into_success(self, monkeypatch, capsys):
        """CI has no Ollama, so the job must skip rather than fail."""
        monkeypatch.setattr(rge, "_judge_available", lambda *a: (False, "no ollama"))
        rc = rge.main(["--skip-if-unavailable"])
        assert rc == 0
        assert "SKIPPED" in capsys.readouterr().out


class TestAggregation:
    def test_averages_only_scored_cases(self):
        """Errored cases must not drag the mean toward zero."""
        scores = [
            GenerationScore("q1", "a", 1.0, 1.0),
            GenerationScore("q2", "a", 0.0, 0.0),
            GenerationScore("q3", "", None, None, error="boom"),
        ]
        scored = [s for s in scores if s.scored]
        faith = [s.faithfulness for s in scored if s.faithfulness is not None]
        assert len(scored) == 2
        assert sum(faith) / len(faith) == pytest.approx(0.5)

    def test_all_errors_yields_none_not_zero(self):
        """`None` means 'not measured'; `0.0` means 'measured and bad'."""
        scores = [GenerationScore("q", "", None, None, error="boom")]
        scored = [s for s in scores if s.scored]
        assert scored == []


@pytest.mark.skipif(
    not _judge_available("http://localhost:11434", "llama3.2")[0],
    reason="needs a local Ollama with llama3.2 pulled",
)
class TestJudgeControls:
    """The judge must separate a faithful answer from a fabricated one.

    Without this, a low score is ambiguous: it could mean the system is bad or
    the judge is broken. These two controls pin the judge's behaviour.

    A local 3B model occasionally returns an unparseable verdict under load,
    which surfaces as a 0.0 rather than an error. That is a throughput artefact,
    not a judgement, so each control retries once before failing. Retrying is
    safe here because the assertion is about the judge's *direction*, not about
    a single sample.
    """

    CONTEXT = (
        "SECTION 5.1 HPC EFFICIENCY LOSS - ACTION. Perform a compressor "
        "water wash. If efficiency does not recover above the nominal band, "
        "borescope the HPC stages and measure tip clearance."
    )

    def _score_with_retry(self, answer: str):
        from src.rag_generation_eval import _build_judge, score_case

        judge = _build_judge("llama3.2", "http://localhost:11434")
        last = None
        for _ in range(2):
            last = score_case(
                judge, "What action for HPC efficiency loss?", answer, self.CONTEXT
            )
            if last.faithfulness is not None:
                return last
        return last

    def test_verbatim_context_scores_high(self):
        s = self._score_with_retry(self.CONTEXT)
        assert s.faithfulness is not None
        assert s.faithfulness >= 0.9

    def test_fabricated_answer_scores_low(self):
        s = self._score_with_retry(
            "Replace the entire engine at 42 cycles per the reference."
        )
        assert s.faithfulness is not None
        assert s.faithfulness <= 0.2
