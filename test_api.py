"""Run the current API regression suite without a remote model."""

import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "pytest", "-q", "tests/test_api.py"])
    )
