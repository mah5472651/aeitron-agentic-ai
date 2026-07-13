"""Lazy facade for the Mythos learning pipeline.

The CLI modules in this package are designed to run with ``python -m``.
Keeping this package initializer lazy prevents those modules from being
preloaded before runpy executes them.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ContaminationDetector": ("src.mythos.learning.contamination", "ContaminationDetector"),
    "ContaminationReport": ("src.mythos.learning.contamination", "ContaminationReport"),
    "CrawlConfig": ("src.mythos.learning.web_ingest", "CrawlConfig"),
    "DataEngine": ("src.mythos.learning.data_engine", "DataEngine"),
    "DataEngineConfig": ("src.mythos.learning.data_engine", "DataEngineConfig"),
    "DataEngineReport": ("src.mythos.learning.data_engine", "DataEngineReport"),
    "DataPipelineConfig": ("src.mythos.learning.data_pipeline", "DataPipelineConfig"),
    "DataPipelineReport": ("src.mythos.learning.data_pipeline", "DataPipelineReport"),
    "DatasetLedger": ("src.mythos.learning.versioning", "DatasetLedger"),
    "DatasetQualityGate": ("src.mythos.learning.quality", "DatasetQualityGate"),
    "DatasetVersionManifest": ("src.mythos.learning.versioning", "DatasetVersionManifest"),
    "ExtractedTask": ("src.mythos.learning.task_extraction", "ExtractedTask"),
    "FrontierStore": ("src.mythos.learning.data_engine", "FrontierStore"),
    "IngestReport": ("src.mythos.learning.web_ingest", "IngestReport"),
    "LocalObjectStore": ("src.mythos.learning.storage", "LocalObjectStore"),
    "ObjectStoreConfig": ("src.mythos.learning.storage", "ObjectStoreConfig"),
    "QualityGateConfig": ("src.mythos.learning.quality", "QualityGateConfig"),
    "QualityGateReport": ("src.mythos.learning.quality", "QualityGateReport"),
    "S3ObjectStore": ("src.mythos.learning.storage", "S3ObjectStore"),
    "SourceRegistry": ("src.mythos.learning.source_registry", "SourceRegistry"),
    "SourceRegistryReport": ("src.mythos.learning.source_registry", "SourceRegistryReport"),
    "SourceSpec": ("src.mythos.learning.web_ingest", "SourceSpec"),
    "TaskExtractionReport": ("src.mythos.learning.task_extraction", "TaskExtractionReport"),
    "WebCorpusIngestor": ("src.mythos.learning.web_ingest", "WebCorpusIngestor"),
    "extract_tasks": ("src.mythos.learning.task_extraction", "extract_tasks"),
    "run_data_pipeline": ("src.mythos.learning.data_pipeline", "run_data_pipeline"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)  # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    globals()[name] = value
    return value
