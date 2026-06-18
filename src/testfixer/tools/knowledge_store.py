"""
ChromaDB-backed knowledge store for flaky test patterns.
Stores indexed console logs, test reports, and error traces as embeddings
so the Fetcher Agent can retrieve similar past failures via RAG.
"""

import os
import logging
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)

COLLECTION_NAME = "flaky_test_knowledge"
PERSIST_DIR = Path(os.getenv("CHROMA_DB_DIR", "./chroma_db")).resolve()


_ef: Optional[embedding_functions.SentenceTransformerEmbeddingFunction] = None


def _get_embedding_function():
    global _ef
    if _ef is None:
        model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        logger.info("Loading embedding model: %s", model_name)
        _ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name
        )
    return _ef


def get_or_create_collection(name: str = COLLECTION_NAME):
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(PERSIST_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    ef = _get_embedding_function()
    collection = client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def index_document(
    doc_id: str,
    text: str,
    metadata: dict,
    collection_name: str = COLLECTION_NAME,
) -> bool:
    """
    Index a single text document (console log, error stack, etc.).
    doc_id should be unique (e.g. 'job_build_artifact-type').
    """
    if not text or not text.strip():
        logger.warning("Skipping empty document: %s", doc_id)
        return False

    collection = get_or_create_collection(collection_name)
    collection.add(
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata],
    )
    logger.info("Indexed document: %s (%d chars)", doc_id, len(text))
    return True


def query_similar(
    query_text: str,
    n_results: int = 5,
    collection_name: str = COLLECTION_NAME,
) -> list[dict]:
    """
    Retrieve the top-N most similar indexed documents.
    Returns list of {id, document, metadata, distance}.
    """
    if not query_text or not query_text.strip():
        return []

    collection = get_or_create_collection(collection_name)
    results = collection.query(
        query_texts=[query_text],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    if results.get("ids") and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            output.append({
                "id": doc_id,
                "document": results["documents"][0][i] if results.get("documents") else "",
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                "distance": results["distances"][0][i] if results.get("distances") else 0.0,
            })
    return output


def count_documents(collection_name: str = COLLECTION_NAME) -> int:
    collection = get_or_create_collection(collection_name)
    return collection.count()


def clear_collection(collection_name: str = COLLECTION_NAME) -> None:
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(PERSIST_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    try:
        client.delete_collection(collection_name)
        logger.info("Deleted collection: %s", collection_name)
    except Exception:
        pass
