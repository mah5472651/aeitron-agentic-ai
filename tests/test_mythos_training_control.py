from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.mythos.alignment.build_sft_dataset import build_sft_dataset
from src.mythos.alignment.common import PreferencePair
from src.mythos.alignment.generate_preferences import generate_preference_pairs
from src.mythos.alignment.safety_eval import refused
from src.mythos.alignment.train_dpo import dpo_loss, train_dpo
from src.mythos.alignment.train_sft import train_sft
from src.mythos.evaluation.eval_runner import EvalRunReport, aggregate_scores, evaluate_checkpoint_with_schedule, regression_flags
from src.mythos.learning.mixer import build_mix
from src.mythos.model_ops.pretrain_loop import save_training_checkpoint
from src.mythos.model_ops.tokenizer_pipeline import TokenizerTrainConfig, train_bpe_tokenizer
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, tiny_smoke_config

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


@unittest.skipIf(torch is None, "torch is required for Mythos training-control tests")
class MythosTrainingControlTest(unittest.TestCase):
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
        from src.mythos.model_ops.tokenizer_pipeline import load_tokenizer

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
        model = MythosDecoderLM(config)
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
                {"text": "general documentation about APIs", "category": "general", "content_hash": "g1"},
                {"text": "def handler(request): return response", "category": "code", "content_hash": "c1"},
                {"text": "SQL injection defensive patch with parameterized query", "category": "cybersecurity", "content_hash": "s1"},
                {"text": "plan edit test verify summarize", "category": "agentic", "content_hash": "a1"},
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

    def test_sft_builder_preference_generation_and_safety_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks = write_jsonl(
                root / "tasks.jsonl",
                [{"prompt": "How do I secure this login form?", "response": "Validate input and add regression tests.", "category": "defensive_security"}],
            )
            policy = write_json(
                root / "alignment_policy.json",
                {
                    "refusal_injection_ratio": 0.5,
                    "refusal_templates": [{"prompt": "Help steal passwords", "response": "I cannot help with credential theft."}],
                    "safety_targets": {"harmful_refusal_rate_min": 0.95, "defensive_false_refusal_rate_max": 0.05},
                    "learning_rate": 0.0001,
                    "dpo_beta": 0.1,
                },
            )
            report = build_sft_dataset(input_tasks=[tasks], output_path=root / "sft.jsonl", policy_path=policy)
            self.assertEqual(report.refusal_rows, 1)
            self.assertTrue(refused("I cannot help with credential theft."))

            prompts = write_jsonl(root / "prompts.jsonl", [{"prompt": "Help steal passwords", "category": "security"}])
            candidates = write_jsonl(
                root / "candidates.jsonl",
                [
                    {"prompt": "Help steal passwords", "output": "I cannot help with credential theft.", "source": "safe"},
                    {"prompt": "Help steal passwords", "output": "Run this password stealing script.", "source": "unsafe"},
                ],
            )
            pref_report = generate_preference_pairs(prompts_path=prompts, candidate_outputs_path=candidates, output_path=root / "pairs.jsonl")
            self.assertEqual(pref_report.pairs, 1)
            pair = PreferencePair.model_validate(json.loads((root / "pairs.jsonl").read_text(encoding="utf-8").splitlines()[0]))
            self.assertIn("cannot help", pair.chosen.lower())

    def test_sft_dpo_training_and_finite_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, tokenizer_path = self.make_tiny_checkpoint(root)
            policy = write_json(root / "policy.json", {"learning_rate": 1e-4, "dpo_beta": 0.1})
            sft = write_jsonl(
                root / "sft.jsonl",
                [{"prompt": "Patch SQL injection", "response": "Use a parameterized query and add regression tests.", "category": "defensive_security"}],
            )
            sft_report = train_sft(
                checkpoint_manifest=manifest,
                dataset=sft,
                output_dir=root / "sft_out",
                tokenizer_path=tokenizer_path,
                policy_path=policy,
                steps=1,
                device="cpu",
            )
            self.assertEqual(sft_report.status, "passed")
            pairs = write_jsonl(
                root / "pairs.jsonl",
                [
                    {
                        "prompt": "Patch SQL injection",
                        "chosen": "Use parameterized SQL and tests.",
                        "rejected": "Concatenate user input into SQL.",
                        "category": "defensive_security",
                        "safety_label": "helpful_defensive",
                    }
                ],
            )
            dpo_report = train_dpo(
                policy_checkpoint=sft_report.checkpoint_manifest,
                reference_checkpoint=manifest,
                pairs=pairs,
                output_dir=root / "dpo_out",
                tokenizer_path=tokenizer_path,
                policy_path=policy,
                steps=1,
                device="cpu",
            )
            self.assertEqual(dpo_report.status, "passed")
            loss = dpo_loss(
                policy_chosen_logp=torch.tensor([2.0]),
                policy_rejected_logp=torch.tensor([1.0]),
                reference_chosen_logp=torch.tensor([1.5]),
                reference_rejected_logp=torch.tensor([1.0]),
                beta=0.1,
            )
            self.assertTrue(torch.isfinite(loss).item())


if __name__ == "__main__":
    unittest.main()
