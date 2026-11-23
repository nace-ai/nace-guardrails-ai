import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize(
    ("name", "output"),
    [
        ("input_guard", "You can view your balance in the app."),
        ("output_guard", "Find your invoice in Settings > Billing."),
        ("async_guard", "Open Settings to change your password."),
        ("image_guard", "A plain square."),
    ],
)
def test_fake_example(name, output, tmp_path):
    result = subprocess.run(
        [sys.executable, "-E", str(EXAMPLES / f"{name}.py"), "--fake"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert output in result.stdout
