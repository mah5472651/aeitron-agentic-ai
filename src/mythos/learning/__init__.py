"""Learning pipeline facade."""

from src.mythos.learning.contamination import ContaminationDetector, ContaminationReport
from src.mythos.learning.data_engine import DataEngine, DataEngineConfig, DataEngineReport, FrontierStore
from src.mythos.learning.data_pipeline import DataPipelineConfig, DataPipelineReport, run_data_pipeline
from src.mythos.learning.quality import DatasetQualityGate, QualityGateConfig, QualityGateReport
from src.mythos.learning.source_registry import SourceRegistry, SourceRegistryReport
from src.mythos.learning.storage import LocalObjectStore, ObjectStoreConfig, S3ObjectStore
from src.mythos.learning.task_extraction import ExtractedTask, TaskExtractionReport, extract_tasks
from src.mythos.learning.versioning import DatasetLedger, DatasetVersionManifest
from src.mythos.learning.web_ingest import CrawlConfig, IngestReport, SourceSpec, WebCorpusIngestor

__all__ = [
    "ContaminationDetector",
    "ContaminationReport",
    "CrawlConfig",
    "DataEngine",
    "DataEngineConfig",
    "DataEngineReport",
    "DatasetLedger",
    "DataPipelineConfig",
    "DataPipelineReport",
    "DatasetQualityGate",
    "DatasetVersionManifest",
    "ExtractedTask",
    "FrontierStore",
    "IngestReport",
    "LocalObjectStore",
    "ObjectStoreConfig",
    "QualityGateConfig",
    "QualityGateReport",
    "S3ObjectStore",
    "SourceSpec",
    "SourceRegistry",
    "SourceRegistryReport",
    "TaskExtractionReport",
    "WebCorpusIngestor",
    "extract_tasks",
    "run_data_pipeline",
]
