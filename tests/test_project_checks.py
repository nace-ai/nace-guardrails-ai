import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_formal_target_is_separate_from_qa():
    result = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "formal"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "python formal/run.py"
    qa = subprocess.run(
        ["make", "--no-print-directory", "--dry-run", "qa"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "formal/run.py" not in qa.stdout


def test_ruff_does_not_collect_formal_tools_or_caches(tmp_path):
    (tmp_path / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
    for path in ("app.py", "formal/.tools/tool.py", ".cache/cached.py"):
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("value = 1\n")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--show-files", "."],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(result.stdout.splitlines()) == {
        str(tmp_path / "app.py"),
        str(tmp_path / "pyproject.toml"),
    }
