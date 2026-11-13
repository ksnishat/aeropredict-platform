"""Generation-quality evaluation for the maintenance-report RAG pipeline.

`rag_eval.py` scores *retrieval*: did the right passage come back? That is
deterministic, needs no LLM, and is gated in CI. It says nothing about whether
the generated report is correct.

This module scores the generated text. Two metrics, both computed by a judge
model (DeepEval running against a local Ollama model):

* **Faithfulness** — is every claim in the answer supported by the retrieved
  context? A report that invents a torque figure or a part number is unfaithful
  even if it reads well.
* **Answer relevancy** — does the answer address the question that was asked?

Why DeepEval and not Ragas: Ragas pins `tenacity<9`, which would downgrade the
9.2.1 already installed and reintroduce the exact incompatibility documented in
`bug_inventory.md` 8.10. DeepEval accepts `tenacity>=8,<=10` and ships a native
Ollama model, so it needs no OpenAI key and no network.

Why this is not in CI: it needs a judge model. A CI runner has no Ollama, and a
metric that silently reports 0.0 when the model is absent is worse than no
metric. The CI gate stays on retrieval; this runs on demand and records its
numbers to MLflow.

Usage:
    python -m src.rag_generation_eval
    python -m src.rag_generation_eval --model llama3.2 --min-faithfulness 0.85
    python -m src.rag_generation_eval --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------- config

DEFAULT_MODEL = os.environ.get("JUDGE_MODEL", "llama3.2")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MIN_FAITHFULNESS = 0.85
DEFAULT_MIN_RELEVANCY = 0.80

# DeepEval computes a per-attempt timeout from the model's context window, which
# for a small local model lands around 88 s. A 3B model on CPU can exceed that
# on a long context, and the failure surfaces as a TimeoutError rather than a
# low score -- which would look like a quality problem when it is a throughput
# problem. Raise it unless the caller has set it explicitly.
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "300")


@dataclass
class GenerationScore:
    """One case's generation metrics."""

    question: str
    answer: str
    faithfulness: float | None
    relevancy: float | None
    error: str | None = None

    @property
    def scored(self) -> bool:
        return self.error is None and self.faithfulness is not None


# ------------------------------------------------------------------ judge setup


def _build_judge(model: str, base_url: str):
    """Return a DeepEval judge model backed by local Ollama.

    Raises a clear error rather than falling back to a default, because a
    silent fallback to an OpenAI model would need a key and would change the
    numbers without saying so.
    """
    try:
        from deepeval.models import OllamaModel  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "deepeval is not installed. Install the eval extra:\n"
            "    pip install deepeval ollama"
        ) from exc

    return OllamaModel(model=model, base_url=base_url, temperature=0)


def _judge_available(base_url: str, model: str) -> tuple[bool, str]:
    """Check that Ollama is reachable and the model is pulled."""
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return False, f"Ollama not reachable at {base_url}: {exc}"

    names = {m.get("name", "") for m in data.get("models", [])}
    # Ollama reports "llama3.2:latest"; accept a bare "llama3.2" too.
    if model in names or f"{model}:latest" in names:
        return True, ""
    return False, (
        f"model {model!r} is not pulled. Available: {sorted(names)}. "
        f"Run: ollama pull {model}"
    )


# ------------------------------------------------------------------- scoring


def score_case(judge, question: str, answer: str, context: str) -> GenerationScore:
    """Score one generated answer with DeepEval's faithfulness and relevancy."""
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric  # noqa: PLC0415
    from deepeval.test_case import LLMTestCase  # noqa: PLC0415

    test_case = LLMTestCase(
        input=question,
        actual_output=answer,
        retrieval_context=[context],
    )

    try:
        faith = FaithfulnessMetric(model=judge, threshold=0.0, include_reason=False)
        faith.measure(test_case)
        faithfulness = float(faith.score)
    except Exception as exc:  # noqa: BLE001 - one bad case must not kill the run
        return GenerationScore(
            question=question,
            answer=answer,
            faithfulness=None,
            relevancy=None,
            error=f"faithfulness: {type(exc).__name__}: {exc}",
        )

    try:
        rel = AnswerRelevancyMetric(model=judge, threshold=0.0, include_reason=False)
        rel.measure(test_case)
        relevancy = float(rel.score)
    except Exception as exc:  # noqa: BLE001
        return GenerationScore(
            question=question,
            answer=answer,
            faithfulness=faithfulness,
            relevancy=None,
            error=f"relevancy: {type(exc).__name__}: {exc}",
        )

    return GenerationScore(
        question=question,
        answer=answer,
        faithfulness=faithfulness,
        relevancy=relevancy,
    )


def run_generation_eval(
    agent,
    cases,
    judge,
    k: int = 4,
) -> dict:
    """Generate an answer per case and score it.

    The answer is produced by the project's own RAG path, not by a bespoke
    prompt, so the metric measures the system that actually ships.
    """
    scores: list[GenerationScore] = []

    for case in cases:
        passages = agent.retrieve(case.question, k=k)
        context = "\n\n---\n\n".join(p["content"] for p in passages)
        try:
            answer = agent.answer_question(case.question, passages)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            scores.append(
                GenerationScore(
                    question=case.question,
                    answer="",
                    faithfulness=None,
                    relevancy=None,
                    error=f"generation: {type(exc).__name__}: {exc}",
                )
            )
            continue

        scores.append(score_case(judge, case.question, answer, context))

    scored = [s for s in scores if s.scored]
    faith_vals = [s.faithfulness for s in scored if s.faithfulness is not None]
    rel_vals = [s.relevancy for s in scored if s.relevancy is not None]

    return {
        "cases": len(scores),
        "scored": len(scored),
        "errors": len(scores) - len(scored),
        "faithfulness": round(sum(faith_vals) / len(faith_vals), 4) if faith_vals else None,
        "answer_relevancy": round(sum(rel_vals) / len(rel_vals), 4) if rel_vals else None,
        "results": [
            {
                "question": s.question,
                "faithfulness": s.faithfulness,
                "answer_relevancy": s.relevancy,
                "answer_chars": len(s.answer),
                "error": s.error,
            }
            for s in scores
        ],
    }


# ------------------------------------------------------------------------ cli


def _load_agent():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from rag_eval import AEROPREDICT_CASES  # noqa: PLC0415
    from rag_inference import MaintenanceRAG  # noqa: PLC0415

    return MaintenanceRAG(), AEROPREDICT_CASES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--k", type=int, default=4)
    parser.add_argument(
        "--min-faithfulness",
        type=float,
        default=DEFAULT_MIN_FAITHFULNESS,
        help=f"Fail below this. Default {DEFAULT_MIN_FAITHFULNESS}.",
    )
    parser.add_argument(
        "--min-relevancy",
        type=float,
        default=DEFAULT_MIN_RELEVANCY,
        help=f"Fail below this. Default {DEFAULT_MIN_RELEVANCY}.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--skip-if-unavailable",
        action="store_true",
        help=(
            "Exit 0 with a clear message when no judge model is available. "
            "Use this in CI, where there is no Ollama. Without it, a missing "
            "model is an error, so a local run cannot silently pass."
        ),
    )
    args = parser.parse_args(argv)

    ok, why = _judge_available(args.ollama_url, args.model)
    if not ok:
        if args.skip_if_unavailable:
            print(f"SKIPPED: {why}")
            return 0
        print(f"ERROR: {why}", file=sys.stderr)
        return 2

    judge = _build_judge(args.model, args.ollama_url)
    agent, cases = _load_agent()
    agent.ingest_knowledge()

    summary = run_generation_eval(agent, cases, judge, k=args.k)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"generation eval: {summary['cases']} cases, judge={args.model}")
        print(f"  scored             {summary['scored']}")
        print(f"  errors             {summary['errors']}")
        faith = summary["faithfulness"]
        rel = summary["answer_relevancy"]
        print(f"  faithfulness       {faith:.3f}" if faith is not None else "  faithfulness       n/a")
        print(f"  answer relevancy   {rel:.3f}" if rel is not None else "  answer relevancy   n/a")
        bad = [r for r in summary["results"] if r["error"]]
        if bad:
            print("\nerrors:")
            for r in bad[:5]:
                print(f"  - {r['question'][:60]}")
                print(f"    {r['error']}")

    if summary["scored"] == 0:
        print("ERROR: no case produced a score", file=sys.stderr)
        return 2

    failed = False
    if summary["faithfulness"] is not None and summary["faithfulness"] < args.min_faithfulness:
        print(
            f"FAIL: faithfulness {summary['faithfulness']:.3f} "
            f"< {args.min_faithfulness}",
            file=sys.stderr,
        )
        failed = True
    if summary["answer_relevancy"] is not None and summary["answer_relevancy"] < args.min_relevancy:
        print(
            f"FAIL: answer relevancy {summary['answer_relevancy']:.3f} "
            f"< {args.min_relevancy}",
            file=sys.stderr,
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
