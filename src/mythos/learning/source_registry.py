"""Allowlisted source registry for defensive Mythos data collection."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from src.mythos.learning.web_ingest import SourceSpec, allowed_url, load_sources
from src.mythos.shared.schemas import StrictModel


APPROVED_LICENSES = {
    "apache-2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "cc-by-4.0",
    "cc0-1.0",
    "mit",
    "mpl-2.0",
    "psf-2.0",
    "public-domain",
    "unknown-ok",
}


class SourceRegistryReport(StrictModel):
    source_count: int
    url_count: int
    domains: list[str]
    categories: list[str]
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

    def validate(self) -> SourceRegistryReport:
        warnings: list[str] = []
        seen_urls: set[str] = set()
        domains: set[str] = set()
        categories: set[str] = set()
        url_count = 0

        for source in self.sources:
            if source.license.lower() not in APPROVED_LICENSES:
                warnings.append(f"{source.name}: license '{source.license}' needs explicit legal approval")
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

        return SourceRegistryReport(
            source_count=len(self.sources),
            url_count=url_count,
            domains=sorted(domains),
            categories=sorted(categories),
            warnings=warnings,
        )

    def to_sources(self) -> list[SourceSpec]:
        return self.sources


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate a Mythos defensive data source registry.")
    parser.add_argument("--sources", required=True)
    args = parser.parse_args()
    report = SourceRegistry.from_file(args.sources).validate()
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
