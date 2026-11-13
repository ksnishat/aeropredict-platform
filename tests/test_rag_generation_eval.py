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


class TestDeterministicMetricDiscriminates:
    """The metric must separate a grounded answer from an invented one.

    This is the control that DeepEval failed. Both of its metrics returned 0.0
    regardless of input against a local llama3.2 judge, so a low score was
    indistinguishable from a broken harness. These cases fail if the
    replacement stops discriminating.
    """

    CONTEXT = (
        "SECTION 5.1 HPC EFFICIENCY LOSS - ACTION. Perform a compressor water "
        "wash. If efficiency does not recover above the nominal band, borescope "
        "the HPC stages and measure tip clearance."
    )
    QUESTION = "What action for HPC efficiency loss?"

    def _score(self, answer: str):
        from src.rag_generation_eval import score_case

        return score_case(None, self.QUESTION, answer, self.CONTEXT)

    def test_verbatim_answer_scores_one(self):
        s = self._score("Perform a compressor water wash.")
        assert s.faithfulness == 1.0
        assert s.unsupported == []

    def test_paraphrase_still_scores_one(self):
        """Paraphrase must pass, or the metric punishes good writing."""
        s = self._score("The reference says to water wash the compressor.")
        assert s.faithfulness == 1.0

    def test_invented_figure_scores_zero(self):
        """A number absent from the context is the failure mode that matters."""
        s = self._score("Replace the turbine blades at 42 cycles and torque to 350 Nm.")
        assert s.faithfulness == 0.0
        assert s.unsupported

    def test_invented_part_number_scores_zero(self):
        s = self._score("Install a new HPT module part number GTX-9000.")
        assert s.faithfulness == 0.0

    def test_mixed_answer_scores_between(self):
        """Half grounded, half invented must land strictly between."""
        s = self._score(
            "Perform a compressor water wash. Install part GTX-9000 at 350 Nm."
        )
        assert s.faithfulness is not None
        assert 0.0 < s.faithfulness < 1.0

    def test_trailing_punctuation_is_not_part_of_the_token(self):
        """`wash.` and `wash` are the same word.

        Treating them as different made a correct paraphrase score 0.0 while a
        verbatim quote scored 1.0 -- an inverted signal.
        """
        from src.rag_generation_eval import _content_tokens

        assert "wash" in _content_tokens("water wash.")
        assert "wash." not in _content_tokens("water wash.")

    def test_metric_is_deterministic(self):
        """Same input, same score -- the property DeepEval did not have."""
        answer = "Perform a compressor water wash."
        scores = {self._score(answer).faithfulness for _ in range(3)}
        assert scores == {1.0}


class TestRelevancyIsDeliberatelyAbsent:
    """Relevancy is not reported, and that decision needs to stay pinned.

    DeepEval's AnswerRelevancy scored a directly relevant one-line answer 0.0
    with the reason "there are no irrelevant statements in the actual output".
    It also scored an answer about Berlin weather 0.0. A metric that cannot
    separate those two is not measuring relevance.
    """

    def test_summary_has_no_answer_relevancy_key(self):
        scores = [GenerationScore("q", "a", 1.0, None)]
        faith = [s.faithfulness for s in scores if s.faithfulness is not None]
        summary = {
            "faithfulness": round(sum(faith) / len(faith), 4) if faith else None,
        }
        assert "answer_relevancy" not in summary

    def test_score_case_reports_no_relevancy(self):
        s = GenerationScore("q", "a", 0.9, None)
        assert s.relevancy is None
