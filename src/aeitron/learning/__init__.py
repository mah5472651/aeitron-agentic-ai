"""Lazy facade for the Aeitron learning pipeline.

The CLI modules in this package are designed to run with ``python -m``.
Keeping this package initializer lazy prevents those modules from being
preloaded before runpy executes them.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ContaminationDetector": ("src.aeitron.learning.contamination", "ContaminationDetector"),
    "ContaminationReport": ("src.aeitron.learning.contamination", "ContaminationReport"),
    "CrawlConfig": ("src.aeitron.learning.web_ingest", "CrawlConfig"),
    "DataEngine": ("src.aeitron.learning.data_engine", "DataEngine"),
    "DataEngineConfig": ("src.aeitron.learning.data_engine", "DataEngineConfig"),
    "DataEngineReport": ("src.aeitron.learning.data_engine", "DataEngineReport"),
    "DataPipelineConfig": ("src.aeitron.learning.data_pipeline", "DataPipelineConfig"),
    "DataPipelineReport": ("src.aeitron.learning.data_pipeline", "DataPipelineReport"),
    "DatasetLedger": ("src.aeitron.learning.versioning", "DatasetLedger"),
    "DatasetQualityGate": ("src.aeitron.learning.quality", "DatasetQualityGate"),
    "DatasetVersionManifest": ("src.aeitron.learning.versioning", "DatasetVersionManifest"),
    "ExtractedTask": ("src.aeitron.learning.task_extraction", "ExtractedTask"),
    "FrontierStore": ("src.aeitron.learning.data_engine", "FrontierStore"),
    "IngestReport": ("src.aeitron.learning.web_ingest", "IngestReport"),
    "LocalObjectStore": ("src.aeitron.learning.storage", "LocalObjectStore"),
    "ObjectStoreConfig": ("src.aeitron.learning.storage", "ObjectStoreConfig"),
    "QualityGateConfig": ("src.aeitron.learning.quality", "QualityGateConfig"),
    "QualityGateReport": ("src.aeitron.learning.quality", "QualityGateReport"),
    "S3ObjectStore": ("src.aeitron.learning.storage", "S3ObjectStore"),
    "SourceRegistry": ("src.aeitron.learning.source_registry", "SourceRegistry"),
    "SourceRegistryReport": ("src.aeitron.learning.source_registry", "SourceRegistryReport"),
    "SourceSpec": ("src.aeitron.learning.web_ingest", "SourceSpec"),
    "TaskExtractionReport": ("src.aeitron.learning.task_extraction", "TaskExtractionReport"),
    "WebCorpusIngestor": ("src.aeitron.learning.web_ingest", "WebCorpusIngestor"),
    "extract_tasks": ("src.aeitron.learning.task_extraction", "extract_tasks"),
    "run_data_pipeline": ("src.aeitron.learning.data_pipeline", "run_data_pipeline"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)  # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
    globals()[name] = value
    return value

