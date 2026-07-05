from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.mythos.learning.quality import DatasetQualityGate, QualityGateConfig
from src.mythos.evaluation.checkpoint_eval import evaluate_checkpoint
from src.mythos.learning.web_ingest import allowed_url, text_from_html
from src.mythos.model_ops.data_loader import TokenShardStream, load_manifest
from src.mythos.model_ops.pretrain_loop import run_pretraining_loop
from src.mythos.model_ops.tokenizer_pipeline import (
    ShardBuildConfig,
    ShardManifest,
    TokenizerTrainConfig,
    build_token_shards,
    train_bpe_tokenizer,
    write_uint32_tokens,
)


class MythosPretrainingPipelineTest(unittest.TestCase):
    def test_quality_gate_filters_duplicates_and_secret_like_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw = root / "raw.jsonl"
            clean = root / "clean.jsonl"
            good_text = "Secure coding guidance for CWE mitigation. " * 20
            rows = [
                {"text": good_text, "license": "mit"},
                {"text": good_text, "license": "mit"},
                {"text": "api_key = 'abcdefghijklmnopqrstuvwxyz123456'", "license": "mit"},
            ]
            raw.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            report = DatasetQualityGate(QualityGateConfig(min_chars=20)).filter_jsonl(raw, clean)
            self.assertEqual(report.accepted, 1)
            self.assertEqual(report.duplicate, 1)
            self.assertEqual(len(clean.read_text(encoding="utf-8").splitlines()), 1)

    def test_web_ingest_helpers_are_allowlist_and_html_safe(self) -> None:
        self.assertTrue(allowed_url("https://docs.example.org/a", ["example.org"]))
        self.assertFalse(allowed_url("https://evil.example.net/a", ["example.org"]))
        self.assertEqual(text_from_html("<html><script>x()</script><body>Hello <b>world</b></body></html>"), "Hello world")

    def test_tokenizer_shards_stream_and_pretrain_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            text = "def secure_login(user, password): assert password CWE mitigation patch " * 200
            corpus.write_text(json.dumps({"text": text, "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            manifest = build_token_shards(
                input_paths=[corpus],
                tokenizer_path=tokenizer_path,
                output_dir=root / "shards",
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.0),
            )
            loaded = load_manifest(root / "shards" / "manifest.json")
            self.assertTrue(loaded.train_shards)
            batch = next(TokenShardStream(loaded.train_shards, sequence_length=16, batch_size=1).batches())
            self.assertEqual(len(batch[0]), 16)
            report = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=2,
                batch_size=1,
                sequence_length=16,
                gradient_accumulation_steps=1,
                dtype="fp32",
                validate_every=0,
                checkpoint_every=1,
                resume=False,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["steps"], 2)
            self.assertEqual(report["validate_every"], 0)
            self.assertTrue(Path(report["checkpoint_manifest"]).exists())
            eval_report = evaluate_checkpoint(
                checkpoint_manifest_path=report["checkpoint_manifest"],
                training_report=report,
                output_dir=root / "checkpoint_eval",
            )
            self.assertEqual(eval_report.status, "passed")
            self.assertTrue(Path(root / "checkpoint_eval" / "checkpoint_eval_report.json").exists())

    def test_checkpoint_eval_reports_validation_interval_not_reached(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            text = "def secure_parser(value): assert value CWE mitigation patch " * 200
            corpus.write_text(json.dumps({"text": text, "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            build_token_shards(
                input_paths=[corpus],
                tokenizer_path=tokenizer_path,
                output_dir=root / "shards",
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.2),
            )
            report = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=1,
                batch_size=1,
                sequence_length=16,
                gradient_accumulation_steps=1,
                dtype="fp32",
                validate_every=25,
                checkpoint_every=0,
                resume=False,
            )
            eval_report = evaluate_checkpoint(
                checkpoint_manifest_path=report["checkpoint_manifest"],
                training_report=report,
                output_dir=root / "checkpoint_eval",
            )
            validation_gate = next(gate for gate in eval_report.gates if gate.name == "validation_loss")
            self.assertEqual(validation_gate.status, "warn")
            self.assertIn("no validation interval", validation_gate.reason)

    def test_pretrain_loop_reports_small_shards_before_training(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            corpus.write_text(json.dumps({"text": "tiny corpus for one shard", "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            build_token_shards(
                input_paths=[corpus],
                tokenizer_path=tokenizer_path,
                output_dir=root / "shards",
                config=ShardBuildConfig(shard_token_count=128, sequence_length=64, validation_fraction=0.0),
            )
            with self.assertRaisesRegex(ValueError, "not enough training tokens for one batch"):
                run_pretraining_loop(
                    output_dir=root / "train",
                    manifest=root / "shards" / "manifest.json",
                    device="cpu",
                    steps=1,
                    batch_size=4,
                    sequence_length=64,
                    gradient_accumulation_steps=1,
                    dtype="fp32",
                    validate_every=0,
                    checkpoint_every=0,
                    resume=False,
                )

    def test_pretrain_loop_expands_model_vocab_for_large_tokenizer_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            corpus.write_text(json.dumps({"text": "small tokenizer asset", "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            shard = root / "shards" / "train" / "shard-000000.bin"
            write_uint32_tokens(shard, [0, 1, 2, 3000] * 16)
            manifest = ShardManifest(
                dataset_id="unit-test",
                tokenizer_path=str(tokenizer_path),
                output_dir=str(root / "shards"),
                train_shards=[str(shard)],
                val_shards=[],
                train_tokens=64,
                val_tokens=0,
                sequence_length=16,
            )
            (root / "shards").mkdir(parents=True, exist_ok=True)
            (root / "shards" / "manifest.json").write_text(json.dumps(manifest.model_dump()), encoding="utf-8")
            report = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=1,
                batch_size=1,
                sequence_length=16,
                gradient_accumulation_steps=1,
                dtype="fp32",
                validate_every=0,
                checkpoint_every=0,
                resume=False,
            )
            self.assertEqual(report["status"], "passed")
            self.assertGreaterEqual(report["model_config"]["vocab_size"], 3001)


if __name__ == "__main__":
    unittest.main()
