"""Native release gate entrypoint for the final Mythos architecture."""

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys


def main() -> None:
    completed = subprocess.run(  # nosec B603
        [sys.executable, "-m", "unittest", "tests.test_mythos_mvp_foundation"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(
        json.dumps(
            {
                "decision": "release" if completed.returncode == 0 else "block",
                "passed": completed.returncode == 0,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
            indent=2,
        )
    )
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
