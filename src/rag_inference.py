"""
RAG maintenance-report assistant for AeroPredict.

Replaces the previous approach of reading the first five pages of a PDF and
stuffing the raw text into the prompt. That was not retrieval: it sent the same
fixed context regardless of the predicted RUL, and it could not cite which part
of the reference supported a claim.

This module builds a ChromaDB vector index over a turbofan maintenance
reference and retrieves the passages relevant to the specific prediction.

Design notes
------------
* The vector store is persisted to disk and reused across runs.
* Retrieval is exposed separately from generation so the retrieved passages can
  be logged and evaluated independently of the LLM.
* The LLM, embedding model, Ollama host and all paths are environment
  configurable; nothing hardcodes a specific Ollama tag.
* If the LLM is unreachable the function returns a deterministic template
  report rather than raising, so the Airflow DAG task does not fail the run.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# ChromaDB requires sqlite3 >= 3.35.0. The Airflow container runs on Debian
# bullseye, which ships 3.34.1, so importing chromadb there raises
# "Your system has an unsupported version of sqlite3". pysqlite3-binary bundles
# a modern sqlite3; alias it over the stdlib module before chromadb is imported.
try:  # pragma: no cover - depends on the runtime environment
    import sqlite3

    if sqlite3.sqlite_version_info < (3, 35, 0):
        import pysqlite3  # type: ignore[import-not-found]

        import sys

        sys.modules["sqlite3"] = pysqlite3
except ImportError:  # pragma: no cover
    pass

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.llms import Ollama
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_LLM_MODEL = "llama3.2"
DEFAULT_PERSIST_DIR = "data/chroma"
DEFAULT_COLLECTION = "turbofan_maintenance"
DEFAULT_KB_PATH = "knowledge_base/turbofan_maintenance.txt"

PROMPT_TEMPLATE = """You are a senior turbofan propulsion engineer writing a
maintenance report.

Use ONLY the reference material below. If the reference material does not
support a claim, say so instead of guessing.

=== REFERENCE MATERIAL ===
{context}
=== END REFERENCE MATERIAL ===

SITUATION:
Engine #101 has a predicted Remaining Useful Life of {rul} cycles.

TASK:
1. URGENCY - state the RUL band and urgency from the reference.
2. DEGRADATION MODE - name the most likely mode and cite the specific sensor
   channels from the reference that support it.
3. ACTION - give the maintenance action the reference prescribes.
4. UNCERTAINTY - state any limitation that applies.
"""


def _urgency_band(rul: float) -> str:
    """Deterministic band lookup, used for the fallback report."""
    if rul > 125:
        return "Nominal (Low urgency)"
    if rul >= 80:
        return "Watch (Low urgency)"
    if rul >= 50:
        return "Elevated (Medium urgency)"
    if rul >= 25:
        return "High (High urgency)"
    return "Critical (High urgency)"


class MaintenanceRAG:
    """Retrieval-augmented maintenance report generator."""

    def __init__(
        self,
        knowledge_base_path: Optional[str] = None,
        persist_directory: Optional[str] = None,
        collection_name: Optional[str] = None,
        embedding_model: Optional[str] = None,
        llm_model: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
    ) -> None:
        self.kb_path = knowledge_base_path or os.getenv(
            "KNOWLEDGE_BASE_PATH", DEFAULT_KB_PATH
        )
        self.persist_directory = persist_directory or os.getenv(
            "CHROMA_PERSIST_DIR", DEFAULT_PERSIST_DIR
        )
        self.collection_name = collection_name or os.getenv(
            "CHROMA_COLLECTION", DEFAULT_COLLECTION
        )
        self.embedding_model_name = embedding_model or os.getenv(
            "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        )
        self.llm_model_name = llm_model or os.getenv("OLLAMA_MODEL", DEFAULT_LLM_MODEL)
        self.ollama_base_url = ollama_base_url or os.getenv(
            "OLLAMA_HOST", "http://localhost:11434"
        )

        self.embeddings = HuggingFaceEmbeddings(model_name=self.embedding_model_name)
        self.llm = Ollama(model=self.llm_model_name, base_url=self.ollama_base_url)
        self.vector_db: Optional[Chroma] = None
        self.chain: Any = None

    # ------------------------------------------------------------------ ingest

    def ingest_knowledge(self, force_rebuild: bool = False) -> int:
        """Build (or reuse) the vector index. Returns the number of chunks."""
        if not os.path.exists(self.kb_path):
            raise FileNotFoundError(f"Knowledge base not found: {self.kb_path}")

        os.makedirs(self.persist_directory, exist_ok=True)

        if not force_rebuild and self._index_exists():
            self.vector_db = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory,
            )
            count = self.vector_db._collection.count()
            if count > 0:
                self._build_chain()
                return count

        # Chroma.from_documents appends to an existing collection rather than
        # replacing it, so a rebuild must drop the collection first or stale
        # chunks from a previous version of the document survive.
        if force_rebuild and self._index_exists():
            stale = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory,
            )
            stale.delete_collection()

        documents = TextLoader(self.kb_path, encoding="utf-8").load()

        # The reference uses long '====' rules as section dividers. Left in
        # place they become their own chunks and dominate retrieval, so strip
        # them and split on paragraph boundaries instead.
        for doc in documents:
            doc.page_content = "\n".join(
                line
                for line in doc.page_content.splitlines()
                if set(line.strip()) not in ({"="}, {"-"}) and line.strip()
            )

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=900,
            chunk_overlap=150,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(documents)

        self.vector_db = Chroma.from_documents(
            documents=chunks,
            embedding=self.embeddings,
            collection_name=self.collection_name,
            persist_directory=self.persist_directory,
        )
        self._build_chain()
        return len(chunks)

    def _index_exists(self) -> bool:
        return os.path.isdir(self.persist_directory) and bool(
            os.listdir(self.persist_directory)
        )

    def _build_chain(self) -> None:
        prompt = PromptTemplate(
            template=PROMPT_TEMPLATE, input_variables=["context", "rul"]
        )
        self.chain = prompt | self.llm | StrOutputParser()

    # --------------------------------------------------------------- retrieval

    def retrieve(self, query: str, k: int = 4) -> list:
        """Return the retrieved passages with their similarity scores."""
        if self.vector_db is None:
            raise RuntimeError("Vector store not initialised; call ingest_knowledge()")
        results = self.vector_db.similarity_search_with_score(query, k=k)
        return [
            {"content": doc.page_content, "score": float(score)}
            for doc, score in results
        ]

    # -------------------------------------------------------------- generation

    def generate_report(self, rul_prediction: float) -> str:
        """Generate a maintenance report for a predicted RUL."""
        if self.chain is None or self.vector_db is None:
            raise RuntimeError("RAG chain not initialised; call ingest_knowledge()")

        query = (
            f"Engine predicted RUL is {rul_prediction:.0f} cycles. "
            "What is the urgency band, the most likely degradation mode with "
            "its supporting sensor channels, and the required maintenance action?"
        )

        passages = self.retrieve(query, k=4)
        context = "\n\n---\n\n".join(p["content"] for p in passages)
        return self.chain.invoke({"context": context, "rul": f"{rul_prediction:.0f}"})


def _fallback_report(rul_prediction: float) -> str:
    """Deterministic report used when the LLM is unavailable."""
    band = _urgency_band(rul_prediction)
    return (
        f"MAINTENANCE REPORT (template fallback - LLM unavailable)\n"
        f"Predicted RUL: {rul_prediction:.0f} cycles\n"
        f"Urgency band: {band}\n"
        f"Recommended action: apply the maintenance action for the confirmed "
        f"degradation mode per the turbofan maintenance reference. Confirm the "
        f"mode with at least two independent sensor channels before acting.\n"
        f"Note: this report was produced without retrieval or LLM generation."
    )


def generate_maintenance_report(rul_prediction: float) -> str:
    """
    Entry point used by the Airflow DAG.

    Returns a report string. Never raises: on any failure it returns a
    deterministic template report so the DAG task does not fail the run.
    """
    print(f"GenAI technician activating for RUL: {rul_prediction}...")

    try:
        agent = MaintenanceRAG()
        count = agent.ingest_knowledge()
        print(f"Retrieval index ready ({count} chunks)")

        passages = agent.retrieve(
            f"RUL {rul_prediction:.0f} cycles urgency band and degradation mode", k=4
        )
        print(f"Retrieved {len(passages)} passages:")
        for p in passages:
            print(f"  score={p['score']:.4f}  {p['content'][:70]!r}")

        report = agent.generate_report(rul_prediction)
        print("\nTURBOFAN MAINTENANCE REPORT:\n")
        print("=" * 60)
        print(report)
        print("=" * 60)
        return report

    except Exception as exc:  # noqa: BLE001 - the DAG must not fail on LLM issues
        print(f"RAG generation failed ({exc}); using template fallback")
        return _fallback_report(rul_prediction)


if __name__ == "__main__":
    generate_maintenance_report(23)
