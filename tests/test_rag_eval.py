"""Tests for the retrieval-evaluation harness.

The harness is a measuring instrument, so it needs its own tests: a metric that
silently reports success is worse than no metric. These pin the scoring rules
that are easy to get subtly wrong — whitespace normalisation, the difference
between a retrieval failure and a ranking weakness, and the gate threshold.
"""

import pytest

from src import rag_eval
from src.rag_eval import EvalCase, evaluate_case, summarise


def _passages(*texts):
    return [{"content": t, "score": float(i)} for i, t in enumerate(texts)]


class TestNormalisation:
    def test_token_split_across_a_line_break_still_matches(self):
        """The manuals are hard-wrapped, so evidence spans lines."""
        case = EvalCase(
            question="q",
            expected_section="s",
            evidence=("12 eggs per second",),
        )
        passages = _passages("rated for 12 eggs per\n    second at most")
        assert evaluate_case(case, passages).hit

    def test_matching_is_case_insensitive(self):
        case = EvalCase(question="q", expected_section="s", evidence=("water wash",))
        assert evaluate_case(case, _passages("Perform a WATER WASH.")).hit


class TestScoring:
    def test_missing_evidence_is_a_retrieval_failure(self):
        case = EvalCase(question="q", expected_section="s", evidence=("T30", "Ps30"))
        result = evaluate_case(case, _passages("unrelated text"))
        assert not result.hit
        assert result.missing_evidence == ["T30", "Ps30"]

    def test_evidence_in_a_later_passage_still_counts_as_a_hit(self):
        """k=4 exists so the right passage can be found at any rank."""
        case = EvalCase(question="q", expected_section="s", evidence=("T30",))
        result = evaluate_case(case, _passages("noise", "noise", "T30 rises"))
        assert result.hit
        assert result.rank == 3

    def test_forbidden_token_in_a_later_passage_is_not_a_failure(self):
        """A neighbouring section in the context is harmless: the LLM sees both.

        Only the top-ranked passage is checked, because that is the one a short
        context window or a summarising prompt leans on.
        """
        case = EvalCase(
            question="q",
            expected_section="s",
            evidence=("T24",),
            forbidden=("BPR decreases",),
        )
        result = evaluate_case(case, _passages("T24 rises", "BPR decreases"))
        assert result.hit
        assert result.forbidden_hits == []

    def test_forbidden_token_in_the_top_passage_is_flagged(self):
        case = EvalCase(
            question="q",
            expected_section="s",
            evidence=("T24",),
            forbidden=("BPR decreases",),
        )
        result = evaluate_case(case, _passages("BPR decreases and T24 rises"))
        assert result.forbidden_hits == ["BPR decreases"]

    def test_rank_is_the_first_passage_holding_all_evidence(self):
        case = EvalCase(question="q", expected_section="s", evidence=("a", "b"))
        result = evaluate_case(case, _passages("a only", "a and b"))
        assert result.rank == 2


class TestSummary:
    def test_recall_counts_hits_and_mrr_rewards_early_ranks(self):
        cases = (
            EvalCase(question="q1", expected_section="s", evidence=("a",)),
            EvalCase(question="q2", expected_section="s", evidence=("b",)),
        )
        results = [
            evaluate_case(cases[0], _passages("a")),  # rank 1
            evaluate_case(cases[1], _passages("noise", "b")),  # rank 2
        ]
        summary = summarise(results, k=4)
        assert summary["recall_at_k"] == 1.0
        assert summary["hit_at_1"] == 0.5
        assert summary["mrr"] == pytest.approx((1.0 + 0.5) / 2, abs=1e-4)

    def test_a_total_miss_lowers_recall(self):
        cases = (
            EvalCase(question="q1", expected_section="s", evidence=("a",)),
            EvalCase(question="q2", expected_section="s", evidence=("b",)),
        )
        results = [
            evaluate_case(cases[0], _passages("a")),
            evaluate_case(cases[1], _passages("nothing")),
        ]
        summary = summarise(results, k=4)
        assert summary["recall_at_k"] == 0.5
        assert len(summary["failures"]) == 1

    def test_empty_case_set_does_not_divide_by_zero(self):
        assert summarise([], k=4) == {"cases": 0}


class TestCaseSetIntegrity:
    """The cases are data, and bad data would make the gate meaningless."""

    def test_every_case_has_evidence(self):
        for case in rag_eval.AEROPREDICT_CASES:
            assert case.evidence, f"{case.question} has no evidence tokens"

    def test_evidence_tokens_actually_exist_in_the_knowledge_base(self):
        """A typo in an evidence token would look like a retrieval failure."""
        from pathlib import Path

        kb = (
            Path(__file__).resolve().parents[1]
            / "knowledge_base"
            / "turbofan_maintenance.txt"
        )
        text = rag_eval._normalise(kb.read_text(encoding="utf-8"))
        for case in rag_eval.AEROPREDICT_CASES:
            for token in case.evidence:
                assert rag_eval._normalise(token) in text, (
                    f"evidence token {token!r} for {case.question!r} "
                    "is not in the knowledge base"
                )

    def test_forbidden_tokens_also_exist_in_the_knowledge_base(self):
        from pathlib import Path

        kb = (
            Path(__file__).resolve().parents[1]
            / "knowledge_base"
            / "turbofan_maintenance.txt"
        )
        text = rag_eval._normalise(kb.read_text(encoding="utf-8"))
        for case in rag_eval.AEROPREDICT_CASES:
            for token in case.forbidden:
                assert rag_eval._normalise(token) in text, (
                    f"forbidden token {token!r} for {case.question!r} "
                    "is not in the knowledge base"
                )

    def test_questions_are_unique(self):
        questions = [c.question for c in rag_eval.AEROPREDICT_CASES]
        assert len(questions) == len(set(questions))
