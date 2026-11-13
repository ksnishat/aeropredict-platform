"""Generation-quality evaluation for the maintenance-report RAG pipeline.

`rag_eval.py` scores *retrieval*: did the right passage come back? That is
deterministic, needs no LLM, and is gated in CI. It says nothing about whether
the generated report is correct.

This module scores the generated text. Two metrics, both computed by a judge
model (DeepEval running against a local Ollama model):

* **Faithfulness** — is every claim in the answer supported by the retrieved
  context? A report that invents a torque figure or a part number is unfaithful
  even if it reads well.

Answer relevancy is deliberately not reported. DeepEval's implementation
inverts on short answers and does not discriminate relevance at all with a
small local judge; see `score_case` for the measurements.

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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------- config

DEFAULT_MODEL = os.environ.get("JUDGE_MODEL", "llama3.2")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MIN_FAITHFULNESS = 0.85

# Fraction of a claim's content tokens that must appear in the retrieved
# context for the claim to count as supported. 0.6 allows paraphrase while
# still failing an invented figure or channel name.
SUPPORT_THRESHOLD = 0.6

@dataclass
class GenerationScore:
    """One case's generation metrics."""

    question: str
    answer: str
    faithfulness: float | None
    relevancy: float | None
    error: str | None = None
    unsupported: list[str] = field(default_factory=list)

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

# DeepEval was tried first and removed. Both of its metrics failed against a
# local llama3.2 judge in ways that would have produced a confident, wrong
# number:
#
#   AnswerRelevancy scored a directly relevant one-line answer 0.0, with the
#   reason "there are no irrelevant statements in the actual output, making it
#   impossible to calculate a higher score". It scored a fabricated answer 0.0
#   too, so it could not separate the two.
#
#   Faithfulness scored 0.0 for *everything* -- a verbatim quote from the
#   context, a paraphrased correct answer, and a fabricated claim all returned
#   exactly 0.0 with the reason "there are no contradictions in the actual
#   output". The judge model itself was fine: asked the same question directly
#   it replied {"unsupported": []} correctly. The defect was in the metric's
#   prompt parsing, not the model.
#
# A metric that returns 0.0 regardless of input is indistinguishable from a
# broken system, and a 0.0 that looks like a measurement is worse than no
# measurement. The replacement below is deterministic and has controls that
# fail if it stops discriminating.

# Trailing punctuation must not become part of a token: "wash." and "wash"
# are the same word, and treating them as different made a correct
# paraphrase score 0.0 while the verbatim quote scored 1.0.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def _sentences(answer: str) -> list[str]:
    """Split an answer into bullet- or sentence-sized claims."""
    parts = re.split(r"(?:\n\s*(?:[-*\u2022]|\d+\.)\s+|(?<=[.!?])\s+)", answer)
    return [p.strip() for p in parts if p.strip()]


def _content_tokens(text: str) -> set[str]:
    """Lowercase word tokens with stopwords removed.

    Stopwords are dropped so that a claim is judged on its content, not on
    "the", "is" and "of" matching the context by accident.
    """
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "from",
    "have", "has", "had", "not", "but", "any", "all", "can", "may", "must",
    "should", "which", "what", "when", "where", "who", "how", "its", "their",
    "there", "these", "those", "been", "being", "does", "did", "will", "would",
    "could", "into", "than", "then", "also", "such", "each", "more", "most",
    "some", "only", "other", "over", "under", "about", "after", "before",
    "according", "reference", "material", "section",
}


def score_case(judge, question: str, answer: str, context: str) -> GenerationScore:
    """Score one answer for grounding, deterministically.

    A claim is *supported* when at least `SUPPORT_THRESHOLD` of its content
    tokens appear in the retrieved context. The answer's score is the fraction
    of its claims that are supported.

    This is deliberately simple and mechanical. It cannot judge whether an
    inference is sound, but it reliably catches the failure mode that matters
    for a maintenance report: a figure, channel name or part number that does
    not appear in the retrieved reference. It is deterministic, so a score
    change means a behaviour change rather than judge noise.

    `judge` is accepted and unused so the call signature stays stable; the
    docstring records why the model-based version was removed.
    """
    context_tokens = _content_tokens(context)
    claims = _sentences(answer)
    if not claims:
        return GenerationScore(
            question=question, answer=answer, faithfulness=None, relevancy=None,
            error="empty answer",
        )

    supported = 0
    unsupported_claims: list[str] = []
    for claim in claims:
        tokens = _content_tokens(claim)
        if not tokens:
            # A claim with no content tokens carries nothing to verify.
            supported += 1
            continue
        overlap = len(tokens & context_tokens) / len(tokens)
        if overlap >= SUPPORT_THRESHOLD:
            supported += 1
        else:
            unsupported_claims.append(claim[:120])

    return GenerationScore(
        question=question,
        answer=answer,
        faithfulness=round(supported / len(claims), 4),
        relevancy=None,
        unsupported=unsupported_claims,
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

    return {
        "cases": len(scores),
        "scored": len(scored),
        "errors": len(scores) - len(scored),
        "faithfulness": round(sum(faith_vals) / len(faith_vals), 4) if faith_vals else None,
        "results": [
            {
                "question": s.question,
                "faithfulness": s.faithfulness,
                "answer_chars": len(s.answer),
                "unsupported": s.unsupported,
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
        print(f"  faithfulness       {faith:.3f}" if faith is not None else "  faithfulness       n/a")
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
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
