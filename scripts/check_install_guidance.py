"""Allow the retired command only in the distribution proposal."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = "guardrails hub " + "install"
IMPORT = "from guardrails" + ".hub"
RETIRED = f"The `{COMMAND}` command is retired."
PROPOSAL = Path("docs/distribution-proposal.md")


def main():
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
    )
    matches = 0
    errors = []
    for entry in paths.split("\0"):
        path = Path(entry)
        if not entry or not (ROOT / path).is_file():
            continue
        for number, line in enumerate(
            (ROOT / path).read_text(errors="replace").splitlines(), 1
        ):
            if COMMAND in line or IMPORT in line:
                if path == PROPOSAL and line == RETIRED:
                    matches += 1
                else:
                    errors.append(
                        f"{path}:{number}: obsolete Guardrails install guidance"
                    )
    if matches != 1:
        errors.append("The distribution proposal must contain one retirement sentence.")
    if errors:
        raise SystemExit("\n".join(errors))


if __name__ == "__main__":
    main()
