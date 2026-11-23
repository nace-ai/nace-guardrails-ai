"""Check supported-cell failures and the separation of unsupported versions."""

import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("cell", "status", "expected"),
    [
        (("3.12", "0.6.8", "latest"), "FAIL (suite)", False),
        (("3.12", "0.9.0", "0.6.2"), "FAIL (suite)", True),
        (("3.14", "latest", "latest"), "FAIL (install)", True),
        (("3.10", "0.9.0", "0.6.2"), "PASS", False),
    ],
)
def test_matrix_exit_code_and_report(cell, status, expected, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import compat_matrix

    report = tmp_path / "compatibility.md"
    logs = tmp_path / "logs"
    monkeypatch.setattr(
        compat_matrix, "build", lambda _: (Path("wheel.whl"), Path("sdist.tar.gz"))
    )
    monkeypatch.setattr(
        compat_matrix,
        "measure",
        lambda measured, *args: (
            measured,
            status,
            {"python": cell[0], "guardrails-ai": cell[1], "nace-sdk": cell[2]},
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compat_matrix.py",
            "--cell",
            *cell,
            "--logs",
            str(logs),
            "--report",
            str(report),
        ],
    )
    assert bool(compat_matrix.main()) is expected
    supported, unsupported = report.read_text().split(
        "## Below the declared floor (not supported)"
    )
    row = f"| {cell[0]} | {cell[1]} | {cell[2]} |"
    if cell[1] == "0.6.8":
        assert row not in supported
        assert row in unsupported
    else:
        assert row in supported
        assert row not in unsupported.split("<details>")[0]
