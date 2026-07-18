"""Native release gate entrypoint for the final Aeitron architecture."""

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys

from src.aeitron.production_readiness import run_production_readiness


def main() -> None:
    readiness = run_production_readiness(mode="dev").model_dump()
    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_aeitron_mvp_foundation",
            "tests.test_aeitron_model_foundation",
            "tests.test_aeitron_data_engine",
            "tests.test_aeitron_pretraining_pipeline",
            "tests.test_aeitron_production_hardening",
            "tests.test_aeitron_scratch_decoder",
            "tests.test_aeitron_training_control",
            "tests.test_aeitron_enterprise_readiness",
            "tests.test_aeitron_training_workspace",
            "tests.test_aeitron_agent_collaboration",
            "tests.test_aeitron_agent_execution",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    print(
        json.dumps(
            {
                "decision": "release" if completed.returncode == 0 else "block",
                "passed": completed.returncode == 0,
                "production_readiness": readiness,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
            indent=2,
        )
    )
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()

