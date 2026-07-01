from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.mythos.db import LocalStore
from src.mythos.gateway import api as gateway_api
from src.mythos.indexing import ContextBuilder, RepositoryIndexer


class MythosMvpFoundationTest(unittest.TestCase):
    def test_repository_index_and_context_builder_find_relevant_auth_code(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "src").mkdir()
            (workspace / "tests").mkdir()
            (workspace / "src" / "auth.py").write_text(
                "\n".join(
                    [
                        "import hashlib",
                        "",
                        "def hash_password(password: str) -> str:",
                        "    return hashlib.sha256(password.encode()).hexdigest()",
                        "",
                        "def login_user(username: str, password: str) -> bool:",
                        "    return username == 'admin' and bool(hash_password(password))",
                    ]
                ),
                encoding="utf-8",
            )
            (workspace / "tests" / "test_auth.py").write_text(
                "from src.auth import login_user\n\n"
                "def test_login_user():\n"
                "    assert login_user('admin', 'secret')\n",
                encoding="utf-8",
            )

            with LocalStore(Path(db_dir) / "mythos.sqlite3") as store:
                project = store.create_project(name="demo", repo_path=str(workspace))

                index_report = RepositoryIndexer(store).index_project(project_id=project["id"])
                self.assertEqual(index_report.status, "ready")
                self.assertGreaterEqual(index_report.file_count, 2)
                self.assertGreaterEqual(index_report.symbol_count, 2)

                context = ContextBuilder(store).build(
                    project_id=project["id"],
                    query="fix login password hashing bug in auth",
                    token_budget=4000,
                )
                self.assertGreater(context.estimated_tokens, 1)
                self.assertTrue(any(chunk.path == "src/auth.py" for chunk in context.chunks))
                self.assertIn("login_user", context.prompt_context)

    def test_gateway_project_index_and_context_contract(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as db_dir:
            workspace = Path(workspace_dir)
            (workspace / "auth.py").write_text(
                "def login_user(username, password):\n"
                "    return username == 'admin' and password == 'secret'\n",
                encoding="utf-8",
            )
            original_store = gateway_api.STORE
            replacement_store = LocalStore(Path(db_dir) / "gateway.sqlite3")
            gateway_api.STORE = replacement_store
            try:
                client = TestClient(gateway_api.app)
                create_response = client.post(
                    "/v1/projects",
                    json={"name": "gateway-demo", "repo_path": str(workspace), "default_branch": "main"},
                )
                self.assertEqual(create_response.status_code, 200, create_response.text)
                project_id = create_response.json()["id"]

                index_response = client.post(f"/v1/projects/{project_id}/index", json={"force": True})
                self.assertEqual(index_response.status_code, 200, index_response.text)
                self.assertEqual(index_response.json()["status"], "ready")

                context_response = client.post(
                    "/v1/context/build",
                    json={
                        "project_id": project_id,
                        "query": "login auth password",
                        "token_budget": 4000,
                        "max_chunks": 5,
                    },
                )
                self.assertEqual(context_response.status_code, 200, context_response.text)
                payload = context_response.json()
                self.assertIn("login_user", payload["prompt_context"])
                self.assertTrue(payload["chunks"])
            finally:
                replacement_store.close()
                gateway_api.STORE = original_store


if __name__ == "__main__":
    unittest.main()
