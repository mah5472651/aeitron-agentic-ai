from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.mythos.model_ops.gpu_smoke import run_scratch_gpu_smoke
from src.mythos.model_ops.torch_decoder import MythosDecoderLM, require_torch, tiny_smoke_config

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


@unittest.skipIf(torch is None, "torch is not installed")
class MythosScratchDecoderTest(unittest.TestCase):
    def test_tiny_decoder_forward_backward(self) -> None:
        require_torch()
        config = tiny_smoke_config()
        model = MythosDecoderLM(config)
        input_ids = torch.randint(0, config.vocab_size, (2, 16))
        output = model(input_ids, labels=input_ids)
        self.assertEqual(tuple(output.logits.shape), (2, 16, config.vocab_size))
        self.assertIsNotNone(output.loss)
        output.loss.backward()
        grad_count = sum(1 for parameter in model.parameters() if parameter.grad is not None)
        self.assertGreater(grad_count, 0)

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
