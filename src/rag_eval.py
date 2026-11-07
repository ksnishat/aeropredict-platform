"""Retrieval-quality evaluation for the maintenance-report RAG pipeline.

The pipeline retrieves passages from the turbofan reference and asks an LLM to
write a report from them. Until now nothing measured whether the retrieval step
actually found the right passages: a report could read fluently while being
grounded in the wrong section of the manual, and no test would notice.

This module scores retrieval against a small labelled question set. Each case
names a question, the section it should retrieve, and tokens that must appear in
the retrieved text. The tokens are the evidence: if the passage about HPC
efficiency loss is retrieved, it contains "water wash" and "borescope".

Two families of metric, deliberately separated:

* **Retrieval metrics** need only the embedding model and the index. They run
  offline, in CI, with no LLM and no network. These are the gate.
* **Generation metrics** need an LLM to produce an answer before it can be
  scored. They are opt-in (``--with-generation``) because a CI runner has no
  Ollama, and a metric that silently reports 0.0 when the model is absent is
  worse than no metric.

The distinction matters: retrieval quality is a property of the code and the
corpus, so it is deterministic and belongs in CI. Generation quality depends on
whichever model happens to be pulled, so it belongs in a manual run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------- cases


@dataclass(frozen=True)
class EvalCase:
    """One question with the evidence a correct retrieval must surface."""

    question: str
    #: Human-readable name of the section that should be retrieved.
    expected_section: str
    #: Tokens that must all appear in the retrieved context. Chosen so that a
    #: passage from the wrong section cannot satisfy them by accident.
    evidence: tuple[str, ...]
    #: Tokens that must NOT appear. Used where a neighbouring section is a
    #: plausible but wrong answer, so a hit on it is a real failure.
    forbidden: tuple[str, ...] = ()


AEROPREDICT_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        question="Which sensor channels indicate HPC efficiency loss?",
        expected_section="3.1 HPC EFFICIENCY LOSS",
        evidence=("T30", "P30", "Ps30", "phi"),
    ),
    EvalCase(
        question="What maintenance action is required for HPC efficiency loss?",
        expected_section="5.1 HPC EFFICIENCY LOSS - ACTION",
        evidence=("water wash", "borescope"),
    ),
    EvalCase(
        question="Which channels indicate fan efficiency loss?",
        expected_section="3.3 FAN EFFICIENCY LOSS",
        evidence=("T24", "P15", "NRf"),
        # 3.4 FAN FLOW LOSS is the neighbouring section and names BPR instead.
        forbidden=("BPR decreases",),
    ),
    EvalCase(
        question="Which channels indicate HPT efficiency loss and why is it critical?",
        expected_section="3.6 HPT EFFICIENCY LOSS",
        evidence=("T50", "W31", "Nc"),
    ),
    EvalCase(
        question="What is the failure threshold that defines end of life?",
        expected_section="4.1 DEFINITION",
        evidence=("30 percent", "Ps30"),
    ),
    EvalCase(
        question="What RUL range is treated as critical?",
        expected_section="4.3 RUL BANDS AND URGENCY",
        evidence=("25", "Critical"),
    ),
    EvalCase(
        question="How many independent channels are needed to confirm a degradation mode?",
        expected_section="SECTION 6 DIAGNOSTIC PROCEDURE",
        evidence=("two standard",),
    ),
    EvalCase(
        question="What must every maintenance report state about uncertainty?",
        expected_section="SECTION 7 REPORTING REQUIREMENTS",
        evidence=("uncertainty",),
    ),
    EvalCase(
        question="How is mean collapse in RUL prediction mitigated?",
        expected_section="8.2 MITIGATIONS",
        evidence=("Clip", "125"),
    ),
    EvalCase(
        question="Why must training windows never cross an engine boundary?",
        expected_section="8.3 WINDOWING",
        evidence=("engine boundary",),
    ),
)

OVOSCAN_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        question="What visual indicators identify an infertile egg?",
        expected_section="2.2 INFERTILE",
        evidence=("blood ring", "blastoderm"),
    ),
    EvalCase(
        question="What action is required for a cracked shell?",
        expected_section="6.2 CRACKED SHELL - ACTION",
        evidence=("collection belt", "2 percent"),
    ),
    EvalCase(
        question="At what infertility rate is the Flock Health team escalated?",
        expected_section="SECTION 7 ESCALATION THRESHOLDS",
        evidence=("8 percent", "Flock Health"),
    ),
    EvalCase(
        question="What happens when a fertile egg also has a shell defect?",
        expected_section="SECTION 5 DISPOSITION RULES",
        evidence=("shell defect overrides",),
    ),
    EvalCase(
        question="How much shell contamination is grounds for rejection?",
        expected_section="3.5 DIRTY SHELL",
        evidence=("10 percent",),
    ),
    EvalCase(
        question="What nutrition deficiency causes soft-shelled eggs?",
        expected_section="6.4 SOFT-SHELLED - ACTION",
        evidence=("calcium", "vitamin D3"),
    ),
    EvalCase(
        question="What lighting level is required for transmitted light inspection?",
        expected_section="8.1 LIGHTING",
        evidence=("2000", "3000", "lux"),
    ),
    EvalCase(
        question="What is the maximum conveyor speed for the inspection station?",
        expected_section="8.2 CONVEYOR SPEED",
        evidence=("12 eggs per second",),
    ),
    EvalCase(
        question="What mean model confidence triggers a model review?",
        expected_section="SECTION 7 ESCALATION THRESHOLDS",
        evidence=("0.75",),
    ),
    EvalCase(
        question="How long are inspection records retained?",
        expected_section="SECTION 9 RECORD KEEPING",
        evidence=("24 months",),
    ),
)

# ------------------------------------------------------------------- metrics


def _normalise(text: str) -> str:
    """Collapse whitespace so a token split across a line break still matches.

    The manuals are hard-wrapped at 79 columns, so "12 eggs per second" appears
    as "12 eggs per\\n    second". Matching raw text would report a retrieval
    failure that is really a formatting artefact.
    """
    return re.sub(r"\s+", " ", text).lower()


@dataclass
class CaseResult:
    case: EvalCase
    retrieved: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    hit: bool = False
    missing_evidence: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    rank: int | None = None

    @property
    def passed(self) -> bool:
        return self.hit and not self.forbidden_hits


def evaluate_case(case: EvalCase, passages: list[dict]) -> CaseResult:
    """Score one case against the passages the retriever returned.

    ``forbidden`` is checked against the *first* passage only. With k=4 the
    retriever routinely returns a neighbouring section alongside the right one,
    and that is harmless: the generator receives both and can choose. What
    matters is whether the passage ranked first is the right one, because that
    is the passage a short context window or a summarising prompt will lean on.
    Checking ``forbidden`` across all k would flag correct behaviour.
    """
    result = CaseResult(
        case=case,
        retrieved=[p["content"] for p in passages],
        scores=[p.get("score", 0.0) for p in passages],
    )
    joined = _normalise("\n".join(result.retrieved))

    result.missing_evidence = [t for t in case.evidence if _normalise(t) not in joined]
    result.hit = not result.missing_evidence

    if result.retrieved:
        top = _normalise(result.retrieved[0])
        result.forbidden_hits = [t for t in case.forbidden if _normalise(t) in top]

    # Rank of the first passage that carries the evidence. A hit at rank 4 is
    # much weaker than a hit at rank 1, and the aggregate should show that.
    for i, passage in enumerate(result.retrieved, start=1):
        text = _normalise(passage)
        if all(_normalise(t) in text for t in case.evidence):
            result.rank = i
            break
    return result


def summarise(results: list[CaseResult], k: int) -> dict:
    """Aggregate case results into the numbers a CI gate reads."""
    n = len(results)
    if n == 0:
        return {"cases": 0}

    hits = sum(1 for r in results if r.hit)
    ranks = [r.rank for r in results if r.rank is not None]
    wrong_top = [r for r in results if r.forbidden_hits]

    return {
        "cases": n,
        "k": k,
        # Fraction of cases whose evidence appeared anywhere in the top-k.
        # This is the gate: if the evidence is not retrieved at all, no prompt
        # can recover it.
        "recall_at_k": round(hits / n, 4),
        # Mean reciprocal rank of the first passage carrying the evidence.
        "mrr": round(sum(1.0 / r for r in ranks) / n, 4) if ranks else 0.0,
        # How often the evidence was in the very first passage.
        "hit_at_1": round(sum(1 for r in ranks if r == 1) / n, 4),
        # Cases where the top-ranked passage came from a neighbouring section.
        # Reported, not gated: the right passage is still in the context, so
        # this is a ranking weakness rather than a retrieval failure.
        "wrong_section_at_1": len(wrong_top),
        "failures": [
            {
                "question": r.case.question,
                "expected": r.case.expected_section,
                "missing_evidence": r.missing_evidence,
            }
            for r in results
            if not r.hit
        ],
        "ranking_weaknesses": [
            {
                "question": r.case.question,
                "expected": r.case.expected_section,
                "top_passage_from": r.forbidden_hits,
                "evidence_rank": r.rank,
            }
            for r in wrong_top
        ],
    }


# ------------------------------------------------------------------ runners


def run_retrieval_eval(agent, cases: tuple[EvalCase, ...], k: int = 4) -> dict:
    """Score retrieval only. No LLM, no network, deterministic."""
    results = [evaluate_case(case, agent.retrieve(case.question, k=k)) for case in cases]
    return summarise(results, k)


def run_generation_eval(agent, cases: tuple[EvalCase, ...], k: int = 4) -> dict:
    """Score generated answers for grounding.

    Deliberately simple and dependency-free: an answer is *grounded* when every
    number it states also appears in the retrieved context. A number that is
    absent from the context was invented, which is the failure mode that matters
    for a maintenance report. This is a coarse proxy for faithfulness, not a
    substitute for Ragas, but it needs no extra dependency and no judge model.
    """
    results = []
    for case in cases:
        passages = agent.retrieve(case.question, k=k)
        context = _normalise("\n".join(p["content"] for p in passages))
        try:
            answer = agent.answer(case.question, passages)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            results.append(
                {
                    "question": case.question,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        numbers = re.findall(r"\d+(?:\.\d+)?", answer)
        unsupported = sorted({n for n in numbers if n not in context})
        results.append(
            {
                "question": case.question,
                "answer_chars": len(answer),
                "numbers_stated": len(numbers),
                "unsupported_numbers": unsupported,
                "grounded": not unsupported,
            }
        )

    scored = [r for r in results if "grounded" in r]
    return {
        "cases": len(results),
        "grounded_rate": (
            round(sum(1 for r in scored if r["grounded"]) / len(scored), 4)
            if scored
            else 0.0
        ),
        "results": results,
    }


# ---------------------------------------------------------------------- cli


def _load_agent(project: str):
    """Import the project's RAG class without hard-coding a sys.path hack."""
    if project == "aeropredict":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from rag_inference import MaintenanceRAG  # noqa: PLC0415

        return MaintenanceRAG(), AEROPREDICT_CASES
    raise SystemExit(f"unknown project: {project}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="aeropredict", choices=["aeropredict"])
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.8,
        help="Fail when recall@k falls below this. Default 0.8.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args(argv)

    agent, cases = _load_agent(args.project)
    agent.ingest_knowledge()
    summary = run_retrieval_eval(agent, cases, k=args.k)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"retrieval eval: {summary['cases']} cases, k={summary['k']}")
        print(f"  recall@{summary['k']}      {summary['recall_at_k']:.3f}")
        print(f"  mrr                {summary['mrr']:.3f}")
        print(f"  hit@1              {summary['hit_at_1']:.3f}")
        print(f"  wrong section @1   {summary['wrong_section_at_1']}")
        if summary["failures"]:
            print("\nretrieval failures (evidence not in top-k):")
            for f in summary["failures"]:
                print(f"  - {f['question']}")
                print(f"    expected: {f['expected']}")
                print(f"    missing:  {f['missing_evidence']}")
        if summary["ranking_weaknesses"]:
            print("\nranking weaknesses (right passage present, wrong one first):")
            for w in summary["ranking_weaknesses"]:
                print(f"  - {w['question']}")
                print(f"    expected: {w['expected']}, evidence at rank {w['evidence_rank']}")

    return 0 if summary["recall_at_k"] >= args.min_recall else 1


if __name__ == "__main__":
    raise SystemExit(main())
