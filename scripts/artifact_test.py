"""Test the wheel and sdist from separate, clean installations."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from installed_suite import build, create_env, install, stage_suite, verify


def main():
    with TemporaryDirectory(prefix="drex-artifacts-") as temporary:
        path = Path(temporary)
        for artifact in build(path / "dist"):
            print(f"Testing {artifact.name}", flush=True)
            target = path / artifact.name
            python = create_env(target / "venv", sys.executable)
            install(python, artifact)
            verify(python, stage_suite(target / "suite"))


if __name__ == "__main__":
    main()
