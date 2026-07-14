from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.aeitron.learning.quality import DatasetQualityGate, QualityGateConfig
from src.aeitron.evaluation.checkpoint_eval import evaluate_checkpoint
from src.aeitron.learning.web_ingest import allowed_url, text_from_html
from src.aeitron.model_ops.data_loader import TokenShardStream, load_manifest
from src.aeitron.model_ops.pretrain_loop import build_cluster_training_plan, load_deepspeed_config, run_pretraining_loop, validate_production_training_args
from src.aeitron.model_ops.native_serving import NativeServingConfig, create_app
from src.aeitron.model_ops.production_adapters import build_megatron_launch_plan, build_tensorrt_llm_plan, export_hf_llama_package, validate_vllm_package
from src.aeitron.model_ops.checkpoint_compare import GenerationConfig, compare_checkpoints
from src.aeitron.model_ops.learning_validation import (
    audit_tokenizer_dominance,
    run_learning_validation,
    write_expanded_eval_suite,
    write_instruction_corpus,
)
from src.aeitron.model_ops.tokenizer_pipeline import (
    RealCorpusTokenizerConfig,
    ShardBuildConfig,
    ShardManifest,
    TokenizerTrainConfig,
    build_token_shards,
    train_real_corpus_tokenizer,
    train_bpe_tokenizer,
    write_uint32_tokens,
)
from src.aeitron.model_ops.torch_decoder import model_profile


class AeitronPretrainingPipelineTest(unittest.TestCase):
    def test_learning_validation_generates_instruction_corpus_and_tokenizer_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = write_instruction_corpus(root / "instruction.jsonl", count=24, repeats=2)
            suite = write_expanded_eval_suite(root / "suite.jsonl", count=50)
            self.assertEqual(len(corpus.read_text(encoding="utf-8").splitlines()), 48)
            self.assertEqual(len(suite.read_text(encoding="utf-8").splitlines()), 50)
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            audit = audit_tokenizer_dominance(
                tokenizer_path=tokenizer_path,
                corpus_path=corpus,
                output_path=root / "tokenizer_audit.json",
            )
            self.assertGreater(audit.total_tokens, 0)
            self.assertFalse(audit.special_token_missing)
            self.assertLess(audit.dot_fraction, 0.08)
            self.assertTrue(Path(root / "tokenizer_audit.json").exists())

    def test_learning_validation_report_can_skip_expensive_overfit_for_local_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_learning_validation(
                output_dir=Path(temp_dir) / "learning_validation",
                instruction_count=30,
                run_overfit=False,
                device="cpu",
            )
            self.assertEqual(report.status, "passed")
            self.assertTrue(Path(report.instruction_corpus_path).exists())
            self.assertTrue(Path(report.expanded_eval_suite_path).exists())
            self.assertIn("--model-profile t4_validation", report.t4_validation_command)

    def test_t4_validation_profile_is_registered_for_non_tiny_gpu_checks(self) -> None:
        profile = model_profile("t4_validation")
        self.assertEqual(profile.hidden_size, 512)
        self.assertEqual(profile.num_layers, 8)
        self.assertEqual(profile.max_sequence_length, 2048)
        self.assertTrue(profile.gradient_checkpointing)

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
            self.assertTrue(Path(report["best_checkpoint_manifest"]).exists())
            self.assertIn("best_validation_loss", report)
            eval_report = evaluate_checkpoint(
                checkpoint_manifest_path=report["best_checkpoint_manifest"],
                training_report=report,
                output_dir=root / "checkpoint_eval",
            )
            self.assertEqual(eval_report.status, "passed")
            self.assertTrue(Path(root / "checkpoint_eval" / "checkpoint_eval_report.json").exists())

    def test_real_corpus_tokenizer_audits_special_tokens_and_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            corpus.write_text(
                "\n".join(
                    json.dumps({"text": "def secure_query(value):\n    return validate(value) 0x7ffd00ff <|compile_error|> " * 20})
                    for _ in range(4)
                )
                + "\n",
                encoding="utf-8",
            )
            report = train_real_corpus_tokenizer(
                RealCorpusTokenizerConfig(
                    input_paths=[str(corpus)],
                    output_dir=str(root / "tokenizer_run"),
                    vocab_size=1200,
                    min_frequency=1,
                    shard_token_count=128,
                    sequence_length=16,
                    validation_fraction=0.0,
                )
            )
            self.assertEqual(report.status, "passed")
            self.assertFalse(report.special_tokens_missing)
            self.assertTrue(Path(report.tokenizer_path).exists())
            self.assertTrue(report.shard_manifest["train_shards"])

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

    def test_pretrain_loop_saves_best_checkpoint_and_can_early_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            corpus.write_text(json.dumps({"text": "def secure_parser(value): assert value", "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            train_shard = root / "shards" / "train" / "shard-000000.bin"
            val_shard = root / "shards" / "val" / "shard-000000.bin"
            write_uint32_tokens(train_shard, [0, 1, 2, 3] * 128)
            write_uint32_tokens(val_shard, [0, 1, 2, 3] * 64)
            manifest = ShardManifest(
                dataset_id="unit-test",
                tokenizer_path=str(tokenizer_path),
                output_dir=str(root / "shards"),
                train_shards=[str(train_shard)],
                val_shards=[str(val_shard)],
                train_tokens=512,
                val_tokens=256,
                sequence_length=16,
            )
            (root / "shards" / "manifest.json").write_text(json.dumps(manifest.model_dump()), encoding="utf-8")
            report = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=20,
                batch_size=1,
                sequence_length=16,
                gradient_accumulation_steps=1,
                dtype="fp32",
                validate_every=1,
                validation_batches=1,
                early_stopping_patience=1,
                early_stopping_min_delta=10_000.0,
                checkpoint_every=0,
                resume=False,
            )
            self.assertEqual(report["status"], "early_stopped")
            self.assertLess(report["steps"], report["requested_steps"])
            self.assertTrue(Path(report["best_checkpoint_manifest"]).exists())

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

    def test_pretrain_loop_accepts_attention_and_checkpointing_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            corpus.write_text(json.dumps({"text": "def secure(value): return validate(value) " * 80, "license": "mit"}) + "\n", encoding="utf-8")
            tokenizer_path = train_bpe_tokenizer(
                [corpus],
                root / "tokenizer.json",
                TokenizerTrainConfig(vocab_size=1200, min_frequency=1),
            )
            build_token_shards(
                input_paths=[corpus],
                tokenizer_path=tokenizer_path,
                output_dir=root / "shards",
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.0),
            )
            report = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=1,
                batch_size=1,
                sequence_length=16,
                dtype="fp32",
                validate_every=0,
                checkpoint_every=0,
                resume=False,
                attention_impl="eager",
                gradient_checkpointing=True,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["attention_impl"], "eager")
            self.assertTrue(report["model_config"]["gradient_checkpointing"])
            import torch

            manifest_payload = json.loads(Path(report["checkpoint_manifest"]).read_text(encoding="utf-8"))
            checkpoint_payload = torch.load(Path(manifest_payload["checkpoint_dir"]) / "model.pt", map_location="cpu")
            self.assertIn("scheduler", checkpoint_payload)
            self.assertIn("training_args", checkpoint_payload)
            self.assertIn("dataset_manifest_sha256", checkpoint_payload)
            self.assertIn("tokenizer_sha256", checkpoint_payload)
            self.assertIn("git_commit", checkpoint_payload)
            self.assertIn("environment", checkpoint_payload)

    def test_checkpoint_comparison_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            text = "def secure_login(user_input): parameterized query validation test patch " * 300
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
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.0),
            )
            training = run_pretraining_loop(
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
            comparison = compare_checkpoints(
                baseline_manifest=training["checkpoint_manifest"],
                candidate_manifest=training["checkpoint_manifest"],
                tokenizer_path=tokenizer_path,
                output_dir=root / "compare",
                device="cpu",
                generation_config=GenerationConfig(max_new_tokens=8),
            )
            self.assertEqual(comparison.status, "neutral")
            self.assertEqual(comparison.baseline.total, 5)
            self.assertTrue(Path(root / "compare" / "checkpoint_comparison_report.json").exists())
            self.assertTrue(Path(root / "compare" / "checkpoint_comparison_report.md").exists())

    def test_native_serving_loads_scratch_checkpoint_and_returns_chat_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            text = "def validate(value): return value.strip() secure patch test " * 250
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
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.0),
            )
            training = run_pretraining_loop(
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
            app = create_app(
                NativeServingConfig(
                    checkpoint_manifest=training["checkpoint_manifest"],
                    tokenizer_path=str(tokenizer_path),
                    model_name="aeitron-test",
                    device="cpu",
                    auth_enabled=False,
                    quota_enabled=False,
                )
            )
            client = TestClient(app)
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200, ready.text)
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "aeitron-test",
                    "messages": [{"role": "user", "content": "Write a safe validation patch."}],
                    "max_tokens": 2,
                    "temperature": 0.0,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["model"], "aeitron-test")
            self.assertIn("choices", payload)
            self.assertTrue(payload["aeitron"]["scratch_only"])

    def test_hf_export_and_external_runtime_plans_are_real_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = root / "clean.jsonl"
            text = "def secure_patch(value): return value.strip() tokenizer export " * 250
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
                config=ShardBuildConfig(shard_token_count=128, sequence_length=16, validation_fraction=0.0),
            )
            training = run_pretraining_loop(
                output_dir=root / "train",
                manifest=root / "shards" / "manifest.json",
                device="cpu",
                steps=1,
                batch_size=1,
                sequence_length=16,
                dtype="fp32",
                validate_every=0,
                checkpoint_every=0,
                resume=False,
            )
            hf_report = export_hf_llama_package(
                checkpoint_manifest=training["checkpoint_manifest"],
                tokenizer_path=tokenizer_path,
                output_dir=root / "hf",
            )
            self.assertEqual(hf_report.status, "built_not_runtime_proven")
            self.assertTrue((root / "hf" / "model.safetensors").exists())
            self.assertEqual(json.loads((root / "hf" / "config.json").read_text(encoding="utf-8"))["model_type"], "llama")
            vllm = validate_vllm_package(hf_model_dir=root / "hf")
            self.assertIn(vllm.status, {"blocked_missing_dependency", "production_ready_requires_external_service"})
            trt = build_tensorrt_llm_plan(hf_model_dir=root / "hf", output_dir=root / "trt")
            self.assertTrue((root / "trt" / "tensorrt_llm_plan.json").exists())
            self.assertIn("trtllm-build", trt.command)
            megatron = build_megatron_launch_plan(
                manifest=root / "shards" / "manifest.json",
                tokenizer_path=tokenizer_path,
                output_dir=root / "mega",
                model_profile="tiny",
                tensor_parallel=1,
                pipeline_parallel=1,
                data_parallel=1,
                sequence_length=16,
                micro_batch_size=1,
                global_batch_size=1,
                train_iters=1,
                megatron_root=root / "missing-megatron",
            )
            self.assertEqual(megatron.status, "blocked_missing_dependency")

    def test_cluster_training_plan_validates_manifest_and_batch_math(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shard = root / "train.bin"
            write_uint32_tokens(shard, [0, 1, 2, 3] * 64)
            tokenizer_path = root / "tokenizer.json"
            tokenizer_path.write_text("{}", encoding="utf-8")
            manifest = ShardManifest(
                dataset_id="cluster-plan-test",
                tokenizer_path=str(tokenizer_path),
                output_dir=str(root / "shards"),
                train_shards=[str(shard)],
                val_shards=[],
                train_tokens=256,
                val_tokens=0,
                sequence_length=32,
            )
            (root / "shards").mkdir(parents=True, exist_ok=True)
            manifest_path = root / "shards" / "manifest.json"
            manifest_path.write_text(json.dumps(manifest.model_dump()), encoding="utf-8")
            plan = build_cluster_training_plan(
                output_dir=root / "cluster-run",
                manifest=manifest_path,
                model_profile_name="7b",
                strategy="fsdp",
                num_nodes=2,
                gpus_per_node=4,
                sequence_length=128,
                batch_size=2,
                gradient_accumulation_steps=8,
                steps=100,
                dtype="bf16",
            )
            self.assertEqual(plan["strategy"], "fsdp")
            self.assertEqual(plan["total_gpus"], 8)
            self.assertEqual(plan["tokens_per_optimizer_step"], 8 * 2 * 8 * 128)
            self.assertIn("torchrun", plan["command"][0])

    def test_deepspeed_config_loader_patches_runtime_batch_fields(self) -> None:
        config = load_deepspeed_config(
            strategy="deepspeed_zero3",
            config_path="deploy/gpu/deepspeed_zero3.json",
            batch_size=2,
            gradient_accumulation_steps=4,
            dtype="fp16",
        )
        self.assertEqual(config["train_micro_batch_size_per_gpu"], 2)
        self.assertEqual(config["gradient_accumulation_steps"], 4)
        self.assertEqual(config["train_batch_size"], 8)
        self.assertTrue(config["fp16"]["enabled"])
        self.assertFalse(config["bf16"]["enabled"])

    def test_production_training_args_reject_tiny_without_dev_smoke(self) -> None:
        manifest = ShardManifest(
            dataset_id="production-validation",
            tokenizer_path="missing-tokenizer.json",
            output_dir="shards",
            train_shards=["train.bin"],
            val_shards=[],
            train_tokens=1024,
            val_tokens=0,
            sequence_length=128,
        )
        with self.assertRaises(ValueError):
            validate_production_training_args(
                production_mode=True,
                dev_smoke=False,
                model_profile_name="tiny",
                manifest="manifest.json",
                tokenizer_path=None,
                active_manifest=manifest,
                validate_every=10,
                checkpoint_every=10,
                run_steps=100,
            )


if __name__ == "__main__":
    unittest.main()

