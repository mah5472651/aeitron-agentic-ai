from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from src.aeitron.evaluation.eval_runner import EvalRunReport, aggregate_scores, evaluate_checkpoint_with_schedule, regression_flags
from src.aeitron.learning.mixer import build_mix
from src.aeitron.learning.ablation_runner import (
    ArmEvidence,
    BoundArtifact,
    ExperimentAuthority,
    ExperimentManifest,
    StatisticalComparisonReport,
    compare_experiment,
    create_experiment_manifest,
    decide_experiment,
    promote_experiment,
    verify_promotion_chain,
)
from src.aeitron.shared.config_contracts import (
    ScientificExperimentCampaignContract,
    load_scientific_experiment_registry,
)
from src.aeitron.model_ops.pretrain_loop import build_learning_rate_scheduler, save_training_checkpoint
from src.aeitron.model_ops.tokenizer_pipeline import SPECIAL_TOKENS, TokenizerTrainConfig, train_bpe_tokenizer
from src.aeitron.model_ops.torch_decoder import AeitronDecoderLM, tiny_smoke_config

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


@unittest.skipIf(torch is None, "torch is required for Aeitron training-control tests")
class AeitronTrainingControlTest(unittest.TestCase):
    def scientific_campaign(self, experiment_type: str) -> ScientificExperimentCampaignContract:
        common = {
            "campaign_id": f"{experiment_type.replace('_', '-')}-test",
            "experiment_type": experiment_type,
            "description": "A controlled scientific experiment used by the Aeitron unit-test evidence authority.",
            "hypothesis": "Measured immutable evidence should select a candidate only when every hard gate passes.",
            "training_profile_id": "fundamentals-validation",
            "token_budget": 1_000_000,
            "required_evaluation_suites": ["HumanEval", "MBPP", "Security"],
        }
        if experiment_type == "tokenizer_selection":
            common.update(
                {
                    "candidate_vocab_sizes": [32_000, 64_000, 128_000],
                    "tokenizer_seeds": [11, 22, 33],
                }
            )
        elif experiment_type == "architecture_ab":
            common.update(
                {
                    "profile_seeds": {
                        "100m": [11, 22, 33],
                        "100m_moe": [11, 22, 33],
                        "300m": [11, 22, 33],
                        "300m_moe": [11, 22, 33],
                        "1b": [44],
                        "1b_moe": [44],
                    }
                }
            )
        else:
            common.update(
                {
                    "profile_seeds": {
                        "50m": [11, 22, 33],
                        "100m": [11, 22, 33],
                        "300m": [11, 22],
                        "1b": [44],
                    },
                    "profile_token_budgets": {
                        "50m": [1_000_000, 3_000_000, 10_000_000],
                        "100m": [1_000_000, 3_000_000, 10_000_000],
                        "300m": [1_000_000, 3_000_000, 10_000_000],
                        "1b": [3_000_000, 10_000_000, 30_000_000],
                    },
                }
            )
        return ScientificExperimentCampaignContract.model_validate(common)

    def scientific_manifest(
        self,
        root: Path,
        experiment_type: str,
    ) -> ExperimentManifest:
        bindings = {}
        for name in ("dataset", "split", "optimizer", "evaluation"):
            bindings[name] = write_json(root / f"{name}.json", {"name": name, "status": "passed"})
        dataset_hash = hashlib.sha256(bindings["dataset"].read_bytes()).hexdigest()
        vocabularies = [32_000, 64_000, 128_000] if experiment_type == "tokenizer_selection" else [64_000]
        tokenizer_manifests: dict[str, Path] = {}
        for vocab_size in vocabularies:
            tokenizer = root / f"tokenizer-{vocab_size}.json"
            tokenizer.write_text(f'{{"vocab_size":{vocab_size}}}', encoding="utf-8")
            shards = write_json(
                root / f"token-shards-{vocab_size}.json",
                {"status": "passed", "vocab_size": vocab_size},
            )
            manifest_path = write_json(
                root / f"tokenizer-manifest-{vocab_size}.json",
                {
                    "schema_version": 1,
                    "status": "passed",
                    "dataset_id": "scientific-test",
                    "dataset_manifest_path": str(bindings["dataset"].resolve()),
                    "dataset_manifest_sha256": dataset_hash,
                    "tokenizer_path": str(tokenizer.resolve()),
                    "tokenizer_sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest(),
                    "shard_manifest_path": str(shards.resolve()),
                    "shard_manifest_sha256": hashlib.sha256(shards.read_bytes()).hexdigest(),
                    "source_sha256": {},
                    "split_strategy": "pre_split_family_safe",
                    "family_safe_split": True,
                    "vocab_size": vocab_size,
                    "special_tokens": ["<unk>", *SPECIAL_TOKENS],
                },
            )
            key = str(vocab_size) if experiment_type == "tokenizer_selection" else "selected"
            tokenizer_manifests[key] = manifest_path
        return create_experiment_manifest(
            campaign=self.scientific_campaign(experiment_type),
            dataset_manifest=bindings["dataset"],
            split_manifest=bindings["split"],
            optimizer_policy=bindings["optimizer"],
            evaluation_manifest=bindings["evaluation"],
            tokenizer_manifests=tokenizer_manifests,
            container_digest="sha256:" + ("a" * 64),
        )

    def arm_evidence(
        self,
        root: Path,
        manifest: ExperimentManifest,
        arm_id: str,
        *,
        validation_loss: float,
        benchmark: float,
        security: float,
        foundation: float,
        tokens_per_byte: float,
        training_flops: float,
        evaluation_authority: str = "executable_model",
        router_ratio: float | None = None,
    ) -> ArmEvidence:
        arm = next(item for item in manifest.arms if item.arm_id == arm_id)
        tokenizer_key = str(arm.vocab_size) if manifest.campaign.experiment_type == "tokenizer_selection" else "selected"
        tokenizer_manifest = json.loads(
            Path(manifest.tokenizers[tokenizer_key].path).read_text(encoding="utf-8")
        )
        tokenizer_sha256 = str(tokenizer_manifest["tokenizer_sha256"])
        checkpoint = write_json(
            root / f"{arm_id}-checkpoint.json",
            {"arm_id": arm_id, "kind": "checkpoint", "status": "passed"},
        )
        tokenizer_audit = write_json(
            root / f"{arm_id}-tokenizer-audit.json",
            {
                "status": "passed",
                "audit_failures": [],
                "vocab_size_actual": arm.vocab_size,
                "tokenizer_sha256": tokenizer_sha256,
                "token_statistics": {"tokens_per_byte": tokens_per_byte},
            },
        )
        training_report = write_json(
            root / f"{arm_id}-training.json",
            {
                "status": "passed",
                "scratch_only": True,
                "objective": "causal_language_modeling",
                "optimizer_policy_sha256": manifest.bindings["optimizer_policy"].sha256,
                "training_args": {
                    "step_semantics": "optimizer_update_v2",
                    "target_tokens": arm.token_budget,
                },
                "trained_tokens": arm.token_budget,
                "model_config": arm.model_contract,
                "best_validation_loss": validation_loss,
                "checkpoint_reload_verified": True,
                "checkpoint_reload_logit_parity": True,
                "best_checkpoint_manifest": str(checkpoint.resolve()),
                "best_checkpoint_manifest_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                "router_metrics": {
                    "dropped_assignments": 0,
                    "maximum_p99_to_mean_load": router_ratio or 0.0,
                },
            },
        )
        evaluation_report = write_json(
            root / f"{arm_id}-evaluation.json",
            {
                "status": "passed",
                "evaluation_mode": "executable_model",
                "aggregate_score": benchmark,
                "suites": [
                    {
                        "name": name,
                        "kind": (
                            "human_eval_style"
                            if name == "HumanEval"
                            else "mbpp_style"
                            if name == "MBPP"
                            else "custom_security"
                        ),
                        "status": "passed",
                        "score": security if name == "Security" else foundation,
                    }
                    for name in arm.required_evaluation_suites
                ],
            },
        )
        generation_audit = write_json(
            root / f"{arm_id}-generation-audit.json",
            {
                "status": "neutral",
                "evaluation_authority": "diagnostic_keyword",
                "promotion_eligible": False,
                "candidate": {
                    "checkpoint_manifest": str(checkpoint.resolve()),
                    "collapsed_count": 0,
                    "hallucination_rate": 0.0,
                },
            },
        )
        return ArmEvidence(
            arm_id=arm.arm_id,
            status="passed",
            seed=arm.seed,
            objective=manifest.objective,
            dataset_manifest_sha256=manifest.bindings["dataset_manifest"].sha256,
            split_manifest_sha256=manifest.bindings["split_manifest"].sha256,
            optimizer_policy_sha256=manifest.bindings["optimizer_policy"].sha256,
            evaluation_manifest_sha256=manifest.bindings["evaluation_manifest"].sha256,
            model_contract_sha256=arm.model_contract_sha256,
            tokenizer_sha256=tokenizer_sha256,
            tokenizer_vocab_size=arm.vocab_size,
            trained_tokens=arm.token_budget,
            training_flops=training_flops,
            total_parameters=arm.total_parameters,
            active_parameters=arm.active_parameters,
            validation_loss=validation_loss,
            executable_benchmark_score=benchmark,
            foundation_score=foundation,
            security_score=security,
            tokens_per_byte=tokens_per_byte,
            checkpoint_reload_parity=True,
            generation_collapsed=False,
            dropped_tokens=0,
            router_p99_to_mean=router_ratio,
            evaluation_authority=evaluation_authority,
            training_report=BoundArtifact.bind("training", training_report),
            evaluation_report=BoundArtifact.bind("evaluation", evaluation_report),
            generation_audit=BoundArtifact.bind("generation_audit", generation_audit),
            checkpoint_manifest=BoundArtifact.bind("checkpoint", checkpoint),
            tokenizer_audit=BoundArtifact.bind("tokenizer_audit", tokenizer_audit),
        )

    def test_warmup_cosine_scheduler_is_finite_and_decays(self) -> None:
        parameter = torch.nn.Parameter(torch.ones(1))
        optimizer = torch.optim.AdamW([parameter], lr=1e-3)
        scheduler = build_learning_rate_scheduler(
            optimizer,
            total_steps=100,
            warmup_steps=10,
            schedule="cosine",
            minimum_learning_rate_ratio=0.1,
        )
        values = []
        for _ in range(100):
            optimizer.step()
            scheduler.step()
            values.append(optimizer.param_groups[0]["lr"])
        self.assertGreater(max(values[:10]), min(values[:10]))
        self.assertGreater(values[20], values[-1])
        self.assertGreaterEqual(values[-1], 1e-4 - 1e-8)

    def make_tiny_checkpoint(self, root: Path) -> tuple[Path, Path]:
        corpus = write_jsonl(
            root / "corpus.jsonl",
            [
                {"text": "def safe_query(cursor, value): return cursor.execute('select * from users where id=?', [value])"},
                {"text": "I cannot help with credential theft. I can help write defensive detection and tests."},
                {"text": "Patch authentication bugs with validation, regression tests, and safe error handling."},
            ],
        )
        tokenizer_path = train_bpe_tokenizer(
            [corpus],
            root / "tokenizer.json",
            TokenizerTrainConfig(vocab_size=1000, min_frequency=1),
        )
        from src.aeitron.model_ops.tokenizer_pipeline import load_tokenizer

        tokenizer = load_tokenizer(tokenizer_path)
        config = tiny_smoke_config().model_copy(
            update={
                "vocab_size": max(256, tokenizer.get_vocab_size()),
                "max_sequence_length": 64,
                "hidden_size": 64,
                "num_attention_heads": 4,
                "num_key_value_heads": 2,
                "intermediate_size": 128,
            }
        )
        model = AeitronDecoderLM(config)
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        manifest = save_training_checkpoint(
            output_dir=root / "checkpoint",
            model=model,
            optimizer=optimizer,
            config=config,
            step=0,
            trained_tokens=0,
            metrics={"loss": 1.0},
        )
        return manifest, tokenizer_path

    def test_eval_runner_missing_required_and_regression_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _tokenizer = self.make_tiny_checkpoint(root)
            schedule = write_json(
                root / "eval_schedule.json",
                {
                    "strict": True,
                    "benchmarks": [
                        {"name": "missing_required", "kind": "static_jsonl", "required": True, "path": str(root / "missing.jsonl")},
                        {"name": "security_smoke", "kind": "built_in_security", "required": True, "category": "domain"},
                    ],
                },
            )
            report = evaluate_checkpoint_with_schedule(
                checkpoint_manifest=manifest,
                schedule_path=schedule,
                output_dir=root / "eval",
                device="cpu",
            )
            self.assertEqual(report.status, "failed")
            self.assertTrue((root / "eval" / "eval_report.json").exists())
            self.assertIn("overall", aggregate_scores(report.benchmarks))

            previous = EvalRunReport(
                status="passed",
                checkpoint_manifest=str(manifest),
                checkpoint_step=0,
                trained_tokens=0,
                output_dir=str(root / "previous"),
                benchmarks=[],
                aggregate_scores={"overall": 0.90, "domain": 0.90},
                regression_flags=[],
                recommendations=[],
            )
            previous_path = write_json(root / "previous_eval.json", previous.model_dump())
            flags = regression_flags(
                current={"overall": 0.82, "domain": 0.86},
                previous_report_path=previous_path,
                warn_threshold=0.03,
                fail_threshold=0.05,
            )
            self.assertTrue(any(flag.status == "fail" for flag in flags))
            self.assertTrue(any(flag.status == "warn" for flag in flags))

    def test_mixer_respects_holdout_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {"text": "general documentation about APIs", "category": "general", "content_hash": "g1", "quality": {"quality_score": 0.9}},
                {"text": "def handler(request): return response", "category": "code", "content_hash": "c1", "quality": {"quality_score": 0.9}},
                {"text": "SQL injection defensive patch with parameterized query", "category": "cybersecurity", "content_hash": "s1", "quality": {"quality_score": 0.9}},
                {"text": "plan edit test verify summarize", "category": "agentic", "content_hash": "a1", "quality": {"quality_score": 0.9}},
                {"text": "HumanEval holdout", "metadata": {"train_policy": "eval_holdout"}, "content_hash": "h1"},
            ]
            source = write_jsonl(root / "clean.jsonl", rows)
            config = write_json(
                root / "mix.json",
                {
                    "seed": 7,
                    "experiments": [{"name": "balanced", "ratios": {"general": 0.25, "code": 0.25, "cybersecurity": 0.25, "agentic": 0.25}}],
                },
            )
            manifest = build_mix(input_paths=[source], config_path=config, experiment="balanced", output_dir=root / "mix")
            self.assertEqual(manifest.excluded_holdout_rows, 1)
            self.assertEqual(manifest.total_rows, 4)
            self.assertTrue((root / "mix" / "mix_manifest.json").exists())

    def test_scientific_registry_and_iso_active_profiles(self) -> None:
        registry = load_scientific_experiment_registry("config/training_qualification_campaigns.json")
        self.assertEqual(len(registry.scientific_experiments), 3)
        for dense_name, moe_name in (("100m", "100m_moe"), ("300m", "300m_moe"), ("1b", "1b_moe")):
            from src.aeitron.model_ops.foundation import model_profile

            dense = model_profile(dense_name).parameter_report()["active"]
            moe = model_profile(moe_name).parameter_report()["active"]
            self.assertLessEqual(abs(moe - dense) / dense, 0.01)

    def test_tokenizer_selection_uses_smallest_noninferior_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "tokenizer_selection")
            evidence = []
            for arm in manifest.arms:
                scores = {
                    32_000: (4.10, 0.805, 0.80, 0.80, 0.24),
                    64_000: (4.00, 0.81, 0.81, 0.81, 0.22),
                    128_000: (3.98, 0.812, 0.81, 0.81, 0.21),
                }[arm.vocab_size]
                evidence.append(
                    self.arm_evidence(
                        root,
                        manifest,
                        arm.arm_id,
                        validation_loss=scores[0],
                        benchmark=scores[1],
                        security=scores[2],
                        foundation=scores[3],
                        tokens_per_byte=scores[4],
                        training_flops=1e15,
                    )
                )
            report = compare_experiment(manifest, evidence)
            self.assertEqual(report.status, "passed", report.model_dump())
            self.assertIn(report.selected_candidate, {"32000", "64000"})
            selected = next(item for item in report.candidate_comparisons if item.candidate == report.selected_candidate)
            best = max(item.downstream_score for item in report.candidate_comparisons)
            self.assertLessEqual(best - selected.downstream_score, 0.01)

    def test_non_executable_evaluation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "tokenizer_selection")
            arm = manifest.arms[0]
            with self.assertRaisesRegex(ValueError, "executable_model"):
                self.arm_evidence(
                    root,
                    manifest,
                    arm.arm_id,
                    validation_loss=4.0,
                    benchmark=0.8,
                    security=0.8,
                    foundation=0.8,
                    tokens_per_byte=0.2,
                    training_flops=1e15,
                    evaluation_authority="static_keyword",
                )

    def test_experiment_authority_admits_only_report_supported_arm_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "tokenizer_selection")
            experiment_dir = root / "experiment"
            evidence_dir = root / "evidence"
            write_json(experiment_dir / "experiment_manifest.json", manifest.model_dump(mode="json"))
            for arm in manifest.arms:
                item = self.arm_evidence(
                    root / "artifacts",
                    manifest,
                    arm.arm_id,
                    validation_loss=4.0,
                    benchmark=0.8,
                    security=0.8,
                    foundation=0.8,
                    tokens_per_byte=0.22,
                    training_flops=float(6 * arm.active_parameters * arm.token_budget),
                )
                write_json(evidence_dir / f"{arm.arm_id}.json", item.model_dump(mode="json"))
            authority = ExperimentAuthority(experiment_dir)
            comparison = authority.run(evidence_dir=evidence_dir)
            self.assertEqual(comparison.status, "passed", comparison.model_dump())
            first_path = evidence_dir / f"{manifest.arms[0].arm_id}.json"
            tampered = json.loads(first_path.read_text(encoding="utf-8"))
            tampered["validation_loss"] = 1.0
            write_json(first_path, tampered)
            blocked = authority.run(evidence_dir=evidence_dir)
            self.assertEqual(blocked.status, "blocked")
            self.assertTrue(any("validation loss" in item for item in blocked.blockers))

    def test_scientific_manifest_rejects_tokenizer_asset_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "architecture_ab")
            tokenizer_contract = json.loads(
                Path(manifest.tokenizers["selected"].path).read_text(encoding="utf-8")
            )
            Path(tokenizer_contract["tokenizer_path"]).write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tokenizer integrity"):
                manifest.verify()

    def test_tokenizer_selection_blocks_statistically_inconclusive_larger_vocab(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "tokenizer_selection")
            scores = {
                32_000: [0.70, 0.80, 0.90],
                64_000: [0.82, 0.82, 0.82],
                128_000: [0.78, 0.78, 0.78],
            }
            offsets = {vocab: 0 for vocab in scores}
            evidence = []
            for arm in manifest.arms:
                offset = offsets[arm.vocab_size]
                offsets[arm.vocab_size] += 1
                score = scores[arm.vocab_size][offset]
                evidence.append(
                    self.arm_evidence(
                        root,
                        manifest,
                        arm.arm_id,
                        validation_loss=4.0,
                        benchmark=score,
                        security=score,
                        foundation=score,
                        tokens_per_byte=0.20,
                        training_flops=float(6 * arm.active_parameters * arm.token_budget),
                    )
                )
            report = compare_experiment(manifest, evidence)
            self.assertEqual(report.status, "blocked", report.model_dump())
            self.assertIn("not statistically superior", " ".join(report.blockers))

    def test_scaling_campaign_rejects_confounded_token_budgets(self) -> None:
        payload = self.scientific_campaign("scaling_law").model_dump(mode="json")
        payload["profile_token_budgets"] = {
            "50m": [1_000_000, 2_000_000],
            "100m": [3_000_000, 4_000_000],
            "300m": [5_000_000, 6_000_000],
            "1b": [7_000_000, 8_000_000],
        }
        with self.assertRaisesRegex(ValueError, "crossed token budgets"):
            ScientificExperimentCampaignContract.model_validate(payload)

    def test_decision_locks_experiment_against_evidence_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "tokenizer_selection")
            experiment_dir = root / "experiment"
            evidence_dir = root / "evidence"
            write_json(experiment_dir / "experiment_manifest.json", manifest.model_dump(mode="json"))
            for arm in manifest.arms:
                item = self.arm_evidence(
                    root / "artifacts",
                    manifest,
                    arm.arm_id,
                    validation_loss=4.0,
                    benchmark=0.8,
                    security=0.8,
                    foundation=0.8,
                    tokens_per_byte=0.2,
                    training_flops=float(6 * arm.active_parameters * arm.token_budget),
                )
                write_json(evidence_dir / f"{arm.arm_id}.json", item.model_dump(mode="json"))
            authority = ExperimentAuthority(experiment_dir)
            self.assertEqual(authority.run(evidence_dir=evidence_dir).status, "passed")
            self.assertEqual(authority.decide().status, "passed")
            with self.assertRaisesRegex(RuntimeError, "immutable"):
                authority.run(evidence_dir=evidence_dir)
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                authority.decide()

    def test_architecture_ab_falls_back_to_dense_on_non_iso_moe_compute(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "architecture_ab")
            evidence = []
            for arm in manifest.arms:
                is_moe = arm.model_profile.endswith("_moe")
                evidence.append(
                    self.arm_evidence(
                        root,
                        manifest,
                        arm.arm_id,
                        validation_loss=3.8 if is_moe else 4.0,
                        benchmark=0.84 if is_moe else 0.80,
                        security=0.82,
                        foundation=0.82,
                        tokens_per_byte=0.2,
                        training_flops=1.03e15 if is_moe else 1e15,
                        router_ratio=1.10 if is_moe else None,
                    )
                )
            report = compare_experiment(manifest, evidence)
            self.assertEqual(report.status, "passed", report.model_dump())
            self.assertTrue(
                any("FLOP" in blocker for pair in report.architecture_pairs for blocker in pair.blockers)
            )
            self.assertEqual(report.selected_candidate, "1b")

    def test_scaling_law_fit_and_tamper_evident_promotion_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.scientific_manifest(root, "scaling_law")
            evidence = []
            for arm in manifest.arms:
                loss = (
                    1.5
                    + 60.0 * arm.active_parameters ** -0.08
                    + 30.0 * arm.token_budget ** -0.06
                )
                evidence.append(
                    self.arm_evidence(
                        root,
                        manifest,
                        arm.arm_id,
                        validation_loss=loss,
                        benchmark=0.80,
                        security=0.80,
                        foundation=0.80,
                        tokens_per_byte=0.2,
                        training_flops=float(arm.active_parameters * arm.token_budget * 6),
                    )
                )
            comparison = compare_experiment(manifest, evidence)
            self.assertEqual(comparison.status, "passed", comparison.model_dump())
            self.assertLessEqual(comparison.scaling_law.holdout_mape, 0.05)
            decision = decide_experiment(manifest, comparison)
            promotion = promote_experiment(decision)
            self.assertEqual(promotion.status, "promoted")
            write_json(root / "experiment_manifest.json", manifest.model_dump(mode="json"))
            write_json(root / "statistical_comparison.json", comparison.model_dump(mode="json"))
            write_json(root / "experiment_decision.json", decision.model_dump(mode="json"))
            promotion_path = write_json(root / "promotion_decision.json", promotion.model_dump(mode="json"))
            verified = verify_promotion_chain(promotion_path)
            self.assertEqual(verified[0].promotion_sha256, promotion.promotion_sha256)
            tampered = json.loads((root / "statistical_comparison.json").read_text(encoding="utf-8"))
            tampered["selected_candidate"] = "32b"
            write_json(root / "statistical_comparison.json", tampered)
            with self.assertRaisesRegex(ValueError, "modified"):
                verify_promotion_chain(promotion_path)

if __name__ == "__main__":
    unittest.main()

