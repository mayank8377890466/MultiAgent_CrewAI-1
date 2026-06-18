"""
Smoke test for RAG pipeline — index + query the ChromaDB knowledge store.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()


async def main():
    from testfixer.tools.knowledge_store import (
        get_or_create_collection,
        index_document,
        query_similar,
        count_documents,
        clear_collection,
    )

    print("\n" + "=" * 60)
    print("  RAG Knowledge Store Smoke Test")
    print("=" * 60)

    # Clean slate
    clear_collection()
    print("  [OK] Cleared any existing collection")

    # Index some test documents
    docs = [
        {
            "id": "build1_errors_0",
            "text": "ERROR: Timeout waiting for element #login-btn after 30 seconds. StackTrace: at org.openqa.selenium.support.ui.WebDriverWait.timeoutException",
            "meta": {"job_name": "Selenium-Tests", "build_number": "1", "status": "FAILURE", "artifact_type": "console_errors"},
        },
        {
            "id": "build2_errors_0",
            "text": "ERROR: NullPointerException at com.app.LoginPage.clickLogin(LoginPage.java:45). Element locator was null.",
            "meta": {"job_name": "Selenium-Tests", "build_number": "2", "status": "FAILURE", "artifact_type": "console_errors"},
        },
        {
            "id": "build3_errors_0",
            "text": "ERROR: StaleElementReferenceException - element was removed from DOM during test execution",
            "meta": {"job_name": "Selenium-Tests", "build_number": "3", "status": "FAILURE", "artifact_type": "console_errors"},
        },
        {
            "id": "build4_console",
            "text": "BUILD SUCCESS. All 25 tests passed. Total time: 5m 30s. No failures detected.",
            "meta": {"job_name": "Selenium-Tests", "build_number": "4", "status": "SUCCESS", "artifact_type": "console_log"},
        },
    ]

    for doc in docs:
        ok = index_document(doc["id"], doc["text"], doc["meta"])
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] Indexed: {doc['id']}")

    count = count_documents()
    print(f"\n  Documents in store: {count}")

    # Query for similar failures
    print("\n" + "-" * 40)
    print("  Query: 'Timeout waiting for element'")
    results = query_similar("Timeout waiting for element", n_results=3)

    for i, r in enumerate(results):
        sim = round(1.0 - r["distance"], 4)
        build = f"{r['metadata'].get('job_name','?')} #{r['metadata'].get('build_number','?')}"
        print(f"  {i+1}. {build} | sim={sim} | type={r['metadata'].get('artifact_type','?')}")
        print(f"     {r['document'][:120]}...")

    print()

    # Query for null pointer
    print("-" * 40)
    print("  Query: 'NullPointerException element locator null'")
    results = query_similar("NullPointerException element locator null", n_results=2)
    for i, r in enumerate(results):
        sim = round(1.0 - r["distance"], 4)
        print(f"  {i+1}. {r['metadata'].get('job_name','?')} #{r['metadata'].get('build_number','?')} | sim={sim}")
        print(f"     {r['document'][:120]}...")

    print()

    # Query for stale element
    print("-" * 40)
    print("  Query: 'StaleElementException DOM removed'")
    results = query_similar("StaleElementException DOM removed", n_results=2)
    for i, r in enumerate(results):
        sim = round(1.0 - r["distance"], 4)
        print(f"  {i+1}. {r['metadata'].get('job_name','?')} #{r['metadata'].get('build_number','?')} | sim={sim}")
        print(f"     {r['document'][:120]}...")

    # Cleanup
    clear_collection()
    print("\n  [OK] Cleaned up test collection")


if __name__ == "__main__":
    asyncio.run(main())
