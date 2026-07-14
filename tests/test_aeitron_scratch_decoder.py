from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from src.aeitron.model_ops.gpu_smoke import run_scratch_gpu_smoke
from src.aeitron.model_ops.torch_decoder import AeitronDecoderLM, model_profile, require_torch, tiny_smoke_config

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


@unittest.skipIf(torch is None, "torch is not installed")
class AeitronScratchDecoderTest(unittest.TestCase):
    def test_tiny_decoder_forward_backward(self) -> None:
        require_torch()
        config = tiny_smoke_config()
        model = AeitronDecoderLM(config)
        input_ids = torch.randint(0, config.vocab_size, (2, 16))
        output = model(input_ids, labels=input_ids)
        self.assertEqual(tuple(output.logits.shape), (2, 16, config.vocab_size))
        self.assertIsNotNone(output.loss)
        output.loss.backward()
        grad_count = sum(1 for parameter in model.parameters() if parameter.grad is not None)
        self.assertGreater(grad_count, 0)

    def test_decoder_kv_cache_generation_eager_and_export(self) -> None:
        require_torch()
        config = tiny_smoke_config().model_copy(update={"attention_impl": "eager", "max_sequence_length": 64})
        model = AeitronDecoderLM(config)
        input_ids = torch.randint(0, config.vocab_size, (1, 8))
        first = model(input_ids[:, :4], use_cache=True)
        self.assertIsNotNone(first.past_key_values)
        second = model(input_ids[:, 4:5], past_key_values=first.past_key_values, use_cache=True)
        self.assertEqual(tuple(second.logits.shape), (1, 1, config.vocab_size))
        generated = model.generate(input_ids[:, :3], max_new_tokens=2)
        self.assertEqual(tuple(generated.shape), (1, 5))
        with tempfile.TemporaryDirectory() as temp_dir:
            export_dir = model.export_checkpoint(temp_dir)
            self.assertTrue(Path(export_dir, "model.pt").exists())
            serving = json.loads(Path(export_dir, "serving_compatibility.json").read_text(encoding="utf-8"))
            self.assertEqual(serving["format"], "aeitron_decoder_v1")
            self.assertEqual(serving["serving_targets"]["native_aeitron"], "supported")
            self.assertTrue(serving["runtime_features"]["kv_cache"])
            self.assertTrue(Path(export_dir, "generation_config.json").exists())

    def test_large_profile_contracts_are_shape_valid_without_instantiation(self) -> None:
        profile = model_profile("62b")
        self.assertEqual(profile.name, "aeitron-62b-scratch")
        self.assertEqual(profile.max_sequence_length, 262_144)
        self.assertEqual(profile.attention_impl, "auto")
        self.assertTrue(profile.gradient_checkpointing)
        self.assertGreater(profile.parameter_estimate(), 50_000_000_000)

    def test_scratch_gpu_smoke_runs_on_cpu_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_scratch_gpu_smoke(
                device="cpu",
                output_dir=temp_dir,
                batch_size=1,
                sequence_length=16,
                steps=1,
                dtype="fp32",
            )
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["scratch_only"])
            self.assertFalse(report["borrowed_model_used"])
            self.assertTrue(Path(report["checkpoint_manifest"]).exists())
            self.assertTrue(Path(report["checkpoint_dir"], "model.pt").exists())


if __name__ == "__main__":
    unittest.main()

