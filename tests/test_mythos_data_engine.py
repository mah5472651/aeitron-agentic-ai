from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, FrontierStore
from src.mythos.learning.web_ingest import SourceSpec


def _page(title: str, link: str | None = None) -> str:
    body = (
        f"<html><body><h1>{title}</h1>"
        + ("<a href='/child.html'>child</a>" if link else "")
        + (" Defensive secure coding guidance for authentication, validation, "
           "CWE mitigation, safe parsing, regression testing, and patch verification. " * 18)
        + "</body></html>"
    )
    return body


class MythosDataEngineTest(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_engine_crawls_discovers_deduplicates_and_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path == "/robots.txt":
                    return httpx.Response(200, text="User-agent: *\nAllow: /\n")
                if request.url.path == "/seed.html":
                    return httpx.Response(200, text=_page("seed", "child"), headers={"content-type": "text/html"})
                if request.url.path == "/child.html":
                    return httpx.Response(200, text=_page("child"), headers={"content-type": "text/html"})
                return httpx.Response(404, text="missing")

            source = SourceSpec(
                name="offline-defensive-source",
                urls=["https://example.org/seed.html"],
                allowed_domains=["example.org"],
                license="mit",
                category="defensive_security",
            )
            config = DataEngineConfig(
                frontier_path=str(root / "frontier.sqlite3"),
                output_dir=str(root / "raw"),
                clean_output_dir=str(root / "clean"),
                max_docs=2,
                workers=2,
                max_depth=1,
                delay_seconds=0.0,
                shard_rows=1,
                request_timeout_seconds=5.0,
            )

            engine = DataEngine(config)
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.org") as client:
                    report = await engine.run([source], client=client)
            finally:
                engine.close()

            self.assertEqual(report.status, "complete")
            self.assertEqual(report.fetched, 2)
            self.assertEqual(report.accepted, 2)
            self.assertGreaterEqual(report.discovered, 1)
            self.assertEqual(report.failed, 0)
            self.assertTrue(list((root / "raw").glob("raw-*.jsonl")))
            self.assertEqual(len(list((root / "clean").glob("clean-*.jsonl"))), 2)

            store = FrontierStore(root / "frontier.sqlite3")
            try:
                stats = store.stats()
            finally:
                store.close()
            self.assertEqual(stats["urls_done"], 2)
            self.assertEqual(stats["documents_accepted"], 2)


if __name__ == "__main__":
    unittest.main()
