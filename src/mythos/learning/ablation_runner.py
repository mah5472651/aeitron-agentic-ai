"""Run data-mix ablation manifests for all configured experiments."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import Field

from src.mythos.learning.mixer import MixManifest, build_mix, load_mix_config
from src.mythos.shared.schemas import StrictModel


class AblationReport(StrictModel):
    status: str
    mix_config: str
    input_paths: list[str]
    output_dir: str
    experiments: list[dict[str, object]]
    recommendation: str
    created_at_unix: float = Field(default_factory=time.time)


def run_ablation(
    *,
    input_paths: list[str | Path],
    mix_config: str | Path,
    output_dir: str | Path,
    tokenizer_path: str | Path | None = None,
    sequence_length: int = 2048,
) -> AblationReport:
    config = load_mix_config(mix_config)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifests: list[MixManifest] = []
    for experiment in config.experiments:
        manifests.append(
            build_mix(
                input_paths=input_paths,
                config_path=mix_config,
                experiment=experiment.name,
                output_dir=root / experiment.name,
                tokenizer_path=tokenizer_path,
                sequence_length=sequence_length,
            )
        )
    experiments = []
    for manifest in manifests:
        experiments.append(
            {
                "name": manifest.experiment,
                "total_rows": manifest.total_rows,
                "total_tokens": manifest.total_tokens,
                "output_jsonl": manifest.output_jsonl,
                "shard_manifest": manifest.shard_manifest,
                "buckets": [bucket.model_dump() for bucket in manifest.buckets],
            }
        )
    recommendation = "run short scratch pretraining and eval_runner for each mix; promote the best domain score without >3% code/general regression"
    report = AblationReport(
        status="complete",
        mix_config=str(mix_config),
        input_paths=[str(path) for path in input_paths],
        output_dir=str(root),
        experiments=experiments,
        recommendation=recommendation,
    )
    (root / "ablation_report.json").write_text(json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(report, root / "ablation_report.md")
    return report


def write_markdown(report: AblationReport, path: str | Path) -> Path:
    target = Path(path)
    lines = [
        "# Mythos Data Mix Ablation Report",
        "",
        f"- status: {report.status}",
        f"- recommendation: {report.recommendation}",
        "",
        "| experiment | rows | tokens |",
        "|---|---:|---:|",
    ]
    for item in report.experiments:
        lines.append(f"| {item['name']} | {item['total_rows']} | {item['total_tokens']} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mythos data mix ablations.")
    parser.add_argument("--inputs", nargs="+")
    parser.add_argument("--mix-config", default="config/mix_ratios.json")
    parser.add_argument("--base-run-dir", help="Accepted for compatibility; use --inputs for actual data.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--sequence-length", type=int, default=2048)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    inputs = args.inputs
    if not inputs and args.base_run_dir:
        base = Path(args.base_run_dir)
        inputs = [
            str(path)
            for path in sorted(
                list(base.rglob("clean-*.jsonl"))
                + list(base.rglob("*clean*.jsonl"))
                + list(base.rglob("*.mixed.jsonl"))
            )
        ]
    if not inputs:
        raise SystemExit("--inputs or --base-run-dir containing JSONL data is required")
    report = run_ablation(
        input_paths=inputs,
        mix_config=args.mix_config,
        output_dir=args.output_dir,
        tokenizer_path=args.tokenizer_path,
        sequence_length=args.sequence_length,
    )
    print(json.dumps(report.model_dump(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
