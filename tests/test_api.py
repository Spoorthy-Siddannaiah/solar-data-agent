"""FastAPI transport tests over scoped services."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.api as api_module
from backend.api import create_app
from backend.scripts.ingest_data import run_ingestion


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.database = root / "api.db"
        cls.storage = root / "storage"
        run_ingestion(REPO_ROOT / "data", cls.database)
        cls.agent_calls = []

        def fake_agent(user_identifier: str, question: str) -> str:
            cls.agent_calls.append((user_identifier, question))
            return f"Scoped answer for {user_identifier}: {question}"

        cls.app = create_app(
            cls.database, cls.storage, agent_runner=fake_agent
        )
        cls.client_context = TestClient(cls.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        cls.temp_dir.cleanup()

    @staticmethod
    def headers(user: str) -> dict:
        return {"X-Demo-User": user}

    def test_health_and_demo_users(self) -> None:
        health = self.client.get("/health")
        users = self.client.get("/demo-users")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(users.status_code, 200)
        self.assertEqual(len(users.json()), 4)
        self.assertNotIn("company_id", json.dumps(users.json()))
        operator = next(
            user
            for user in users.json()
            if user["user_id"] == "company_1_operator"
        )
        self.assertFalse(operator["can_view_financials"])

    def test_config_reports_only_openai_key_presence(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "not-returned"}):
            configured = self.client.get("/config")
        with patch.dict(os.environ, {}, clear=True):
            missing = self.client.get("/config")

        self.assertEqual(
            configured.json(), {"openai_api_key_configured": True}
        )
        self.assertEqual(missing.json(), {"openai_api_key_configured": False})
        self.assertNotIn("not-returned", configured.text)

    def test_frontend_demo_assets_are_served(self) -> None:
        page = self.client.get("/demo/")
        script = self.client.get("/demo/app.js")
        stylesheet = self.client.get("/demo/styles.css")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Solar Data Agent", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertNotIn("company_id", script.text)
        self.assertEqual(stylesheet.status_code, 200)

    def test_frontend_downloads_use_identity_header_and_blob(self) -> None:
        script = self.client.get("/demo/app.js")

        self.assertEqual(script.status_code, 200)
        self.assertIn('"X-Demo-User": state.selectedUser.user_id', script.text)
        self.assertIn("const blob = await response.blob()", script.text)
        self.assertIn("anchor.download = filename", script.text)
        self.assertIn("function safeFilename", script.text)
        self.assertIn("function protectedDocumentPaths", script.text)
        self.assertIn("parsed.origin === window.location.origin", script.text)
        self.assertIn('parsed.searchParams.get("run_id")', script.text)
        self.assertIn("Download report", script.text)

    def test_chat_uses_validated_demo_identity_and_agent_runner(self) -> None:
        response = self.client.post(
            "/chat",
            headers=self.headers("company_1_operator"),
            json={"question": "List my plants"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("company_1_operator", response.json()["answer"])
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(
            self.agent_calls[-1],
            ("company_1_operator", "List my plants"),
        )
        recovered = self.client.get(
            f"/runs/{response.json()['run_id']}",
            headers=self.headers("company_1_operator"),
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["run_type"], "chat")
        self.assertEqual(recovered.json()["result"], response.json()["answer"])

    def test_default_chat_runner_receives_configured_database_path(self) -> None:
        calls = []

        def configured_agent(
            user_identifier: str,
            question: str,
            *,
            database_path: Path,
            document_storage_path: Path,
        ) -> str:
            calls.append(
                (
                    user_identifier,
                    question,
                    database_path,
                    document_storage_path,
                )
            )
            return "Answer from configured database"

        with patch.object(api_module, "run_agent_question", configured_agent):
            app = create_app(self.database, self.storage)
            with TestClient(app) as client:
                response = client.post(
                    "/chat",
                    headers=self.headers("company_1_operator"),
                    json={"question": "List my plants"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            calls,
            [
                (
                    "company_1_operator",
                    "List my plants",
                    self.database,
                    self.storage,
                )
            ],
        )

    def test_other_user_cannot_recover_chat_run(self) -> None:
        created = self.client.post(
            "/chat",
            headers=self.headers("company_1_operator"),
            json={"question": "List my plants"},
        ).json()

        response = self.client.get(
            f"/runs/{created['run_id']}",
            headers=self.headers("company_1_admin"),
        )
        self.assertEqual(response.status_code, 404)

    def test_unknown_demo_user_is_rejected_before_chat(self) -> None:
        calls_before = len(self.agent_calls)
        response = self.client.post(
            "/chat",
            headers=self.headers("missing-user"),
            json={"question": "List my plants"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.agent_calls), calls_before)

    def test_energy_report_run_lookup_and_download(self) -> None:
        created = self.client.post(
            "/reports/energy",
            headers=self.headers("company_1_operator"),
            json={"format": "xlsx", "start": None, "end": None},
        )
        self.assertEqual(created.status_code, 201)
        metadata = created.json()
        self.assertEqual(metadata["classification"], "energy")
        self.assertNotIn("storage", json.dumps(metadata))
        self.assertNotIn("company_id", json.dumps(metadata))

        run = self.client.get(
            f"/runs/{metadata['run_id']}",
            headers=self.headers("company_1_operator"),
        )
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.json()["run_id"], metadata["run_id"])
        self.assertEqual(run.json()["run_type"], "energy_report")
        self.assertEqual(run.json()["result"], f"Generated {metadata['filename']}")
        self.assertEqual(
            run.json()["documents"][0]["document_id"],
            metadata["document_id"],
        )

        downloaded = self.client.get(
            f"/documents/{metadata['document_id']}/download",
            params={"run_id": metadata["run_id"]},
            headers=self.headers("company_1_operator"),
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertTrue(downloaded.content.startswith(b"PK"))
        self.assertIn("attachment;", downloaded.headers["content-disposition"])

    def test_energy_only_user_cannot_create_financial_report(self) -> None:
        response = self.client.post(
            "/reports/financial",
            headers=self.headers("company_2_operator"),
            json={"format": "pdf", "month": 3},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"], "financial data access is not permitted"
        )

    def test_financial_user_can_create_financial_report(self) -> None:
        response = self.client.post(
            "/reports/financial",
            headers=self.headers("company_2_admin"),
            json={"format": "pdf", "month": 3},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["classification"], "financial")
        self.assertEqual(response.json()["media_type"], "application/pdf")

    def test_same_company_other_user_cannot_access_run_or_download(self) -> None:
        created = self.client.post(
            "/reports/energy",
            headers=self.headers("company_1_operator"),
            json={"format": "docx"},
        ).json()

        run = self.client.get(
            f"/runs/{created['run_id']}",
            headers=self.headers("company_1_admin"),
        )
        download = self.client.get(
            f"/documents/{created['document_id']}/download",
            params={"run_id": created["run_id"]},
            headers=self.headers("company_1_admin"),
        )

        self.assertEqual(run.status_code, 404)
        self.assertEqual(download.status_code, 404)

    def test_other_company_cannot_access_run_or_download(self) -> None:
        created = self.client.post(
            "/reports/energy",
            headers=self.headers("company_1_admin"),
            json={"format": "pdf"},
        ).json()

        run = self.client.get(
            f"/runs/{created['run_id']}",
            headers=self.headers("company_2_admin"),
        )
        download = self.client.get(
            f"/documents/{created['document_id']}/download",
            params={"run_id": created["run_id"]},
            headers=self.headers("company_2_admin"),
        )

        self.assertEqual(run.status_code, 404)
        self.assertEqual(download.status_code, 404)

    def test_openapi_requests_do_not_expose_company_id_or_paths(self) -> None:
        specification = json.dumps(self.client.get("/openapi.json").json())
        self.assertNotIn("company_id", specification)
        self.assertNotIn("database_path", specification)
        self.assertNotIn("filesystem_path", specification)
        self.assertNotIn("storage_root", specification)


if __name__ == "__main__":
    unittest.main()
