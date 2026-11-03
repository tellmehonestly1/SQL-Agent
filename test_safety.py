"""Run shared SQL policy and execution-boundary tests."""

import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_policy.py",
                "tests/test_db.py",
            ]
        )
    )
