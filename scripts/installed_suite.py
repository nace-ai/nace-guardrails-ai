"""Build distributions and run the release suite in isolated installations."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV = os.environ.get("UV", "uv")


def run(command, *, cwd=None, log=None):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update({"LITELLM_LOCAL_MODEL_COST_MAP": "True", "OTEL_SDK_DISABLED": "true"})
    return subprocess.run(
        [str(argument) for argument in command],
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT if log else None,
        check=True,
        text=True,
    )


def build(directory):
    run(
        [sys.executable, "-m", "build", "--installer", "uv", "--outdir", directory],
        cwd=ROOT,
    )
    return next(directory.glob("*.whl")), next(directory.glob("*.tar.gz"))


def stage_suite(directory):
    directory.mkdir(parents=True)
    for name in ("tests", "examples", "eval", "scripts"):
        shutil.copytree(
            ROOT / name,
            directory / name,
            ignore=shutil.ignore_patterns("__pycache__", ".cache", "results"),
        )
    for name in ("README.md", "pyproject.toml", "Makefile"):
        shutil.copy2(ROOT / name, directory)
    return directory


def create_env(directory, python, *, pip=False, log=None):
    flags = ["--seed"] if pip else []
    run([UV, "venv", "--python", python, *flags, directory], log=log)
    return directory / "bin" / "python"


def install(python, artifact, *dependencies, pip=False, log=None):
    command = (
        [python, "-m", "pip", "install"]
        if pip
        else [UV, "pip", "install", "--python", python]
    )
    run([*command, f"{artifact}[dev]", *dependencies], log=log)


def verify(python, directory, *, log=None):
    # The stage contains the tests, examples and evaluation tool. It has no drex_policy source.
    run(
        [
            python,
            "-I",
            "-c",
            (
                "import os, site, sys; from pathlib import Path; "
                "sys.path.insert(0, os.getcwd()); "
                "import drex_policy; origin = Path(drex_policy.__file__).resolve(); "
                "assert any(origin.is_relative_to(Path(p).resolve()) for p in site.getsitepackages()), origin; "
                "print(origin, flush=True); "
                "import pytest; raise SystemExit(pytest.main(['--import-mode=importlib']))"
            ),
        ],
        cwd=directory,
        log=log,
    )
