"""Allowlisted source registry for defensive Aeitron data collection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from src.aeitron.learning.web_ingest import SourceSpec, allowed_url, load_sources
from src.aeitron.shared.schemas import StrictModel


APPROVED_LICENSES = {
    "apache-2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "cc-by-4.0",
    "cc0-1.0",
    "mit",
    "mpl-2.0",
    "postgresql",
    "psf-2.0",
    "public-domain",
    "unknown-ok",
}


class SourceRegistryReport(StrictModel):
    source_count: int
    url_count: int
    domains: list[str]
    categories: list[str]
    approved_sources: int = 0
    quarantine_sources: int = 0
    source_snapshot_sha256: str
    warnings: list[str] = Field(default_factory=list)


class SourceRegistry:
    """Validates and summarizes approved crawl sources.

    This keeps large data jobs governed: every URL must match the source
    allowlist, duplicate seeds are rejected, and license/category metadata is
    auditable before a crawler process starts.
    """

    def __init__(self, sources: list[SourceSpec]) -> None:
        self.sources = sources

    @classmethod
    def from_file(cls, path: str | Path) -> "SourceRegistry":
        return cls(load_sources(path))

    @classmethod
    def from_files(cls, paths: list[str | Path]) -> "SourceRegistry":
        sources: list[SourceSpec] = []
        for path in paths:
            sources.extend(load_sources(path))
        return cls(sources)

    def validate(self, *, production: bool = False) -> SourceRegistryReport:
        warnings: list[str] = []
        seen_urls: set[str] = set()
        seen_ids: set[str] = set()
        domains: set[str] = set()
        categories: set[str] = set()
        url_count = 0
        approved_sources = 0
        quarantine_sources = 0

        for source in self.sources:
            assert source.source_id is not None
            if source.source_id in seen_ids:
                raise ValueError(f"duplicate source_id: {source.source_id}")
            seen_ids.add(source.source_id)
            if source.license.lower() not in APPROVED_LICENSES:
                warnings.append(f"{source.name}: license '{source.license}' needs explicit legal approval")
            if source.approval_status == "approved":
                approved_sources += 1
            else:
                quarantine_sources += 1
                warnings.append(f"{source.name}: source remains {source.approval_status}/{source.trust_tier}")
            if production:
                if source.approval_status != "approved":
                    raise ValueError(f"{source.name}: production collection requires approval_status='approved'")
                if source.trust_tier not in {"reviewed", "trusted"}:
                    raise ValueError(f"{source.name}: production training source must be reviewed or trusted")
                if source.approved_use == "evaluation_only":
                    raise ValueError(f"{source.name}: evaluation-only source cannot enter production training collection")
                if source.immutable_revision == "rolling":
                    raise ValueError(f"{source.name}: production source requires an immutable_revision")
                if source.license_evidence_sha256 is None or source.legal_approval_sha256 is None:
                    raise ValueError(f"{source.name}: production source requires license and legal approval evidence hashes")
            if not source.allowed_domains:
                raise ValueError(f"{source.name}: allowed_domains cannot be empty")
            categories.add(source.category)
            for domain in source.allowed_domains:
                domains.add(domain.lower())
            for url in source.urls:
                url_count += 1
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"}:
                    raise ValueError(f"{source.name}: unsupported URL scheme in {url}")
                if not allowed_url(url, source.allowed_domains):
                    raise ValueError(f"{source.name}: URL outside allowed domains: {url}")
                if url in seen_urls:
                    warnings.append(f"{source.name}: duplicate seed URL ignored by frontier: {url}")
                seen_urls.add(url)

        snapshot_payload = json.dumps(
            [source.model_dump(mode="json") for source in sorted(self.sources, key=lambda item: item.source_id or "")],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        import hashlib

        return SourceRegistryReport(
            source_count=len(self.sources),
            url_count=url_count,
            domains=sorted(domains),
            categories=sorted(categories),
            approved_sources=approved_sources,
            quarantine_sources=quarantine_sources,
            source_snapshot_sha256=hashlib.sha256(snapshot_payload.encode("utf-8")).hexdigest(),
            warnings=warnings,
        )

    def to_sources(self) -> list[SourceSpec]:
        return self.sources

    def approve_source(
        self,
        *,
        source_id: str,
        immutable_revision: str,
        license_evidence_path: str | Path,
        legal_approval_path: str | Path,
        trust_tier: str = "reviewed",
    ) -> SourceSpec:
        if immutable_revision.strip() in {"", "rolling"}:
            raise ValueError("approval requires an immutable source revision")
        if trust_tier not in {"reviewed", "trusted"}:
            raise ValueError("approved source trust tier must be reviewed or trusted")
        license_path = Path(license_evidence_path).resolve(strict=True)
        legal_path = Path(legal_approval_path).resolve(strict=True)
        if not license_path.is_file() or not legal_path.is_file():
            raise ValueError("approval evidence must be regular files")

        def file_hash(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        index = next(
            (position for position, source in enumerate(self.sources) if source.source_id == source_id),
            None,
        )
        if index is None:
            raise KeyError(source_id)
        approved = self.sources[index].model_copy(
            update={
                "immutable_revision": immutable_revision.strip(),
                "license_evidence_sha256": file_hash(license_path),
                "legal_approval_sha256": file_hash(legal_path),
                "trust_tier": trust_tier,
                "approval_status": "approved",
            }
        )
        self.sources[index] = SourceSpec.model_validate(approved.model_dump())
        return self.sources[index]

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sources": [source.model_dump() for source in self.sources]}
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return target


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate or merge Aeitron defensive data source registries.")
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--output")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--approve-source")
    parser.add_argument("--immutable-revision")
    parser.add_argument("--license-evidence")
    parser.add_argument("--legal-approval")
    parser.add_argument("--trust-tier", choices=["reviewed", "trusted"], default="reviewed")
    args = parser.parse_args()
    registry = SourceRegistry.from_files(args.sources)
    if args.approve_source:
        required = {
            "--immutable-revision": args.immutable_revision,
            "--license-evidence": args.license_evidence,
            "--legal-approval": args.legal_approval,
            "--output": args.output,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            parser.error("source approval requires " + ", ".join(missing))
        registry.approve_source(
            source_id=args.approve_source,
            immutable_revision=args.immutable_revision,
            license_evidence_path=args.license_evidence,
            legal_approval_path=args.legal_approval,
            trust_tier=args.trust_tier,
        )
    report = registry.validate(production=args.production)
    if args.output:
        registry.write(args.output)
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

