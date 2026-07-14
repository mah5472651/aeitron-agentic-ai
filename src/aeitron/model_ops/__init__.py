"""Model profile, backend, serving adapters, and scratch foundation contracts."""

from src.aeitron.model_ops.foundation import (
    CheckpointManifest,
    DecoderArchitectureSpec,
    ParallelismPlan,
    PretrainingRunSpec,
    TokenizerContract,
    TrainingDataContract,
    architecture_presets,
    foundation_status,
)
from src.aeitron.model_ops.torch_decoder import AeitronDecoderLM, ScratchDecoderConfig, tiny_smoke_config
from src.aeitron.model_ops.native_serving import NativeServingConfig, create_app as create_native_serving_app
from src.aeitron.model_ops.production_adapters import export_hf_llama_package, validate_vllm_package

__all__ = [
    "CheckpointManifest",
    "DecoderArchitectureSpec",
    "AeitronDecoderLM",
    "NativeServingConfig",
    "ParallelismPlan",
    "PretrainingRunSpec",
    "ScratchDecoderConfig",
    "TokenizerContract",
    "TrainingDataContract",
    "architecture_presets",
    "foundation_status",
    "tiny_smoke_config",
    "create_native_serving_app",
    "export_hf_llama_package",
    "validate_vllm_package",
]

