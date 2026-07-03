from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.mythos.gateway import api as gateway_api
from src.mythos.model_ops.foundation import (
    CheckpointManifest,
    PretrainingRunSpec,
    TokenizerContract,
    TrainingDataContract,
    architecture_presets,
    foundation_status,
)


class MythosModelFoundationTest(unittest.TestCase):
    def test_architecture_presets_are_valid_scratch_specs(self) -> None:
        status = foundation_status()
        self.assertTrue(status["scratch_first"])
        self.assertFalse(status["fine_tune_default"])
        presets = architecture_presets()
        self.assertIn("mythos-7b", presets)
        self.assertIn("mythos-100b", presets)
        for name, spec in presets.items():
            estimate = spec.estimate_parameters()
            self.assertEqual(spec.name, name)
            self.assertGreater(estimate["total"], 0)
            self.assertGreater(estimate["total_billions"], 1)
            self.assertLess(abs(estimate["delta_billions"]), spec.parameter_target_billions)

    def test_pretraining_readiness_requires_real_assets_and_data_gates(self) -> None:
        spec = PretrainingRunSpec(
            architecture=architecture_presets()["mythos-7b"],
            tokenizer=TokenizerContract(tokenizer_path="missing-tokenizer.json"),
            data=TrainingDataContract(manifest_path="missing-dataset.jsonl", token_count_estimate=0),
        )
        report = spec.readiness_report()
        self.assertFalse(report["ready"])
        self.assertTrue(report["missing_assets"])
        self.assertIn("data contamination check is not complete", report["policy_failures"])
        self.assertTrue(report["scratch_training"])
        self.assertFalse(report["fine_tune"])

    def test_checkpoint_manifest_hashes_files_and_writes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checkpoint = root / "ckpt"
            checkpoint.mkdir()
            (checkpoint / "model.safetensors").write_text("weights", encoding="utf-8")
            manifest = CheckpointManifest.from_directory(
                architecture_name="mythos-7b",
                run_id="run-1",
                step=10,
                trained_tokens=1024,
                checkpoint_dir=checkpoint,
                metrics={"loss": 1.25},
            )
            self.assertEqual(len(manifest.files), 1)
            self.assertEqual(manifest.files[0]["path"], "model.safetensors")
            output = manifest.write_atomic(root / "manifest.json")
            self.assertTrue(output.exists())

    def test_gateway_foundation_status_contract(self) -> None:
        client = TestClient(gateway_api.app)
        response = client.get("/v1/model/foundation/status")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["scratch_first"])
        self.assertIn("mythos-7b", payload["presets"])


if __name__ == "__main__":
    unittest.main()
