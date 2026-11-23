"""Measure compatibility with the full offline suite against a built wheel."""

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from installed_suite import (
    ROOT,
    UV,
    build,
    create_env,
    install,
    run,
    stage_suite,
    verify,
)


@dataclass(frozen=True)
class Cell:
    python: str
    guardrails: str
    sdk: str

    @property
    def name(self):
        return f"py{self.python}-gr{self.guardrails}-sdk{self.sdk}"


def declared_floor():
    text = (ROOT / "pyproject.toml").read_text()
    return re.search(r'"guardrails-ai>=(\d+\.\d+\.\d+)"', text)[1]


def full_matrix(floor):
    guardrails = dict.fromkeys([floor, "0.6.8", "0.9.3", "0.10.2", "0.11.0", "latest"])
    pinned = [
        Cell(python, gr, sdk)
        for python in ("3.10", "3.12")
        for gr in guardrails
        for sdk in ("0.6.2", "latest")
    ]
    return sorted(
        set(
            pinned
            + [Cell(python, "latest", "latest") for python in ("3.11", "3.13", "3.14")]
        ),
        key=lambda cell: (
            tuple(map(int, cell.python.split("."))),
            cell.guardrails,
            cell.sdk,
        ),
    )


def requirement(name, requested):
    return name if requested == "latest" else f"{name}=={requested}"


def below_floor(cell):
    if cell.guardrails == "latest":
        return False
    return tuple(map(int, cell.guardrails.split("."))) < tuple(
        map(int, declared_floor().split("."))
    )


def resolved(python):
    code = (
        "import importlib.metadata as m, json, platform; "
        "print(json.dumps({'python':platform.python_version(), "
        "**{p.metadata['Name'].lower():p.version for p in m.distributions()}}))"
    )
    return json.loads(subprocess.check_output([python, "-I", "-c", code], text=True))


def measure(cell, wheel, work, log_dir):
    location = work / cell.name
    pip = cell.python == "3.14"
    log_path = log_dir / f"{cell.name}.log"
    versions = {}
    status = "FAIL (setup)"
    with log_path.open("w") as log:
        try:
            python = create_env(location / "venv", cell.python, pip=pip, log=log)
            versions = resolved(python)
            status = "FAIL (install)"
            dependencies = [
                requirement("guardrails-ai", cell.guardrails),
                requirement("nace-sdk", cell.sdk),
            ]
            if not pip and below_floor(cell):
                override = location / "override.txt"
                override.write_text(dependencies[0] + "\n")
                run(
                    [
                        UV,
                        "pip",
                        "install",
                        "--python",
                        python,
                        "--overrides",
                        override,
                        f"{wheel}[dev]",
                        *dependencies,
                    ],
                    log=log,
                )
            else:
                install(python, wheel, *dependencies, pip=pip, log=log)
            versions = resolved(python)
            status = "FAIL (suite)"
            verify(python, stage_suite(location / "suite"), log=log)
            status = "PASS"
        except subprocess.CalledProcessError as error:
            print(error, file=log)
    print(f"{cell.name} {status} ({log_path})", flush=True)
    return cell, status, versions


def write_report(report, rows):
    heading = (
        "# Offline compatibility\n\n"
        "Each cell installs the 0.2.0 wheel and runs the complete offline suite. "
        "The versions below are the resolved versions. Guardrails 0.11.0 declares Python <3.14. "
        "The Python 3.14 cell uses pip to honor that bound. The preliminary uv smoke test selected 0.11.0. "
        "The other cells use uv.\n\n"
    )
    heading += (
        "The declared Guardrails floor is **0.9.0**. On Python 3.12 with Nace SDK 0.11.2, "
        "the full suite failed on 0.5.15, 0.6.8, each 0.7 release (0.7.0 to 0.7.3), "
        "and each 0.8 release (0.8.0 to 0.8.2). "
        "Guardrails 0.9.0, 0.9.1, 0.9.2, and 0.9.3 passed. "
        "Guardrails 0.6 through 0.8 reject two validator instances in one `Guard.use` call "
        "and retain previous validators on repeated calls. "
        "The test suite checks the documented 0.9 behavior for both calls. "
        "The previous 0.4.0 floor cannot resolve its Pydantic constraint with Nace SDK.\n\n"
        "Cells below the floor use a uv override to measure the suite against the installed wheel. "
        "Nace SDK 0.6.2 includes validator attribution in `X-Nace-Client`. "
        "Its `User-Agent` includes only the SDK version. "
        "The SDK changelog records 0.7.0 as the first release to append client_info to User-Agent. "
        "The tests assert X-Nace-Client for every version and the User-Agent integration from 0.7.0 onward. "
        "[SDK changelog](https://github.com/nace-ai/drex/blob/main/sdk/nace-python/CHANGELOG.md#070)\n\n"
    )
    lines = [heading.rstrip()]
    for title, unsupported in (
        ("Supported versions", False),
        ("Below the declared floor (not supported)", True),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Python request | Guardrails request | SDK request | Python | Guardrails AI | Nace SDK | Pydantic | Result |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for cell, status, versions in rows:
            if below_floor(cell) != unsupported:
                continue
            values = [cell.python, cell.guardrails, cell.sdk]
            values.extend(
                versions.get(package, "Unavailable")
                for package in ("python", "guardrails-ai", "nace-sdk", "pydantic")
            )
            lines.append("| " + " | ".join([*values, status]) + " |")
    lines.extend(
        ["", "<details>", "<summary>Resolved package versions by cell</summary>", ""]
    )
    for cell, _, versions in rows:
        pins = " ".join(
            f"{name}=={value}"
            for name, value in sorted(versions.items())
            if name != "python"
        )
        lines.append(f"- `{cell.name}` " + (f"`{pins}`" if pins else "Unavailable"))
    lines.extend(["", "</details>", ""])
    report.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cell", nargs=3, metavar=("PYTHON", "GUARDRAILS", "SDK"), action="append"
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--logs", type=Path, default=ROOT / ".compat-logs")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    cells = (
        [
            Cell(py, declared_floor() if gr == "floor" else gr, sdk)
            for py, gr, sdk in args.cell
        ]
        if args.cell
        else full_matrix(declared_floor())
    )
    args.logs.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="drex-matrix-") as temporary:
        directory = Path(temporary)
        wheel, _ = build(directory / "dist")
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            rows = list(
                pool.map(lambda cell: measure(cell, wheel, directory, args.logs), cells)
            )
    report = args.report or (None if args.cell else ROOT / "docs" / "compatibility.md")
    if report:
        write_report(report, rows)
    return any(status != "PASS" and not below_floor(cell) for cell, status, _ in rows)


if __name__ == "__main__":
    raise SystemExit(main())
