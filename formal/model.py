"""Check TLC invariants and extract every bounded terminal path from its graph."""

import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = re.compile(r'^(-?\d+) \[label=("(?:\\.|[^"\\])*")(.*)\];?$')
EDGE = re.compile(r'^(-?\d+) -> (-?\d+) \[label="(\w+)"')
VIOLATION = re.compile(r"^Error: Invariant (\w+) is violated\.", re.MULTILINE)
DONE = re.compile(r"(\d+) distinct states found, 0 states left on queue\.")
BASELINES = ("Validation", "Concurrency")
MODEL_MUTANTS = {
    "duplicate_fail": ("Validation", "duplicateFail", "P1First"),
    "pass_after_fail": ("Validation", "passAfterFail", "P1First"),
    "no_finite": ("Validation", "noFinite", "P2Finite"),
    "map_401": ("Validation", "map401", "P2Caller"),
    "catch_cancel": ("Validation", "catchCancelAsAvailability", "P3Native"),
    "catch_cancel_as_availability": (
        "Validation",
        "catchCancelAsAvailability",
        "P3Native",
    ),
    "remove_cancel_flag": ("Validation", "removeCancelFlag", "P3Flag"),
    "remove_replacement_check": ("Validation", "noReplacementCheck", "P1Replacement"),
    "allow_error_replacement": ("Validation", "allowErrorReplacement", "P1Accepted"),
    "masked_availability": ("Validation", "maskedAvailability", "P2"),
    "double_call": ("Validation", "callAgain", "P6"),
    "shared_answer": ("Concurrency", "sharedAnswer", "P8"),
}


def parse_value(raw):
    if raw == "TRUE":
        return True
    if raw == "FALSE":
        return False
    if raw.startswith("<<") and raw.endswith(">>"):
        return tuple(json.loads(f"[{raw[2:-2]}]"))
    return json.loads(raw)


def graph_paths(file):
    nodes, edges, roots = {}, defaultdict(list), []
    for line in file.read_text().splitlines():
        node_match = STATE.fullmatch(line)
        edge_match = EDGE.match(line)
        if node_match:
            key, encoded, style = node_match.groups()
            parts = json.loads(encoded).splitlines()
            nodes[key] = {
                name: parse_value(value)
                for name, value in (
                    part.removeprefix("/\\ ").split(" = ", 1) for part in parts
                )
            }
            if "filled" in style:
                roots.append(key)
        elif edge_match:
            start, end, action = edge_match.groups()
            if start != end:
                edges[start].append((end, action))

    reached = set()

    def walk(key, states, steps):
        reached.add(key)
        states = [*states, nodes[key]]
        if not edges[key]:
            yield states, steps
        for end, action in edges[key]:
            yield from walk(end, states, [*steps, action])

    if not roots:
        raise ValueError(f"TLC graph has no initial states: {file}")
    paths = [path for root in roots for path in walk(root, [], [])]
    if set(nodes) != reached:
        raise ValueError(f"TLC graph has {len(set(nodes) - reached)} unreachable nodes")
    return paths, len(reached)


def tlc(jar, output, module, *, variant="baseline", invariant=None, name=None):
    folder = output / (name or module)
    folder.mkdir()
    shutil.copyfile(HERE / f"{module}.tla", folder / f"{module}.tla")
    config = (HERE / f"{module}.cfg").read_text()
    if invariant:
        config = f'INIT Init\nNEXT Next\nCONSTANT Variant = "{variant}"\nINVARIANT {invariant}\n'
    (folder / f"{module}.cfg").write_text(config)
    cmd = [
        "java",
        "-Xmx256m",
        "-XX:+UseParallelGC",
        "--class-path",
        str(jar),
        str(HERE / "OfflineTLC.java"),
        "-workers",
        "1",
    ]
    if not invariant:
        cmd.extend(("-continue", "-dump", "dot,actionlabels", "paths.dot"))
    cmd.append(module)
    run = subprocess.run(
        cmd,
        check=False,
        cwd=folder,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    log = folder / "tlc.log"
    log.write_text(run.stdout)
    failures = set(VIOLATION.findall(run.stdout))
    if invariant:
        # A named invariant violation is TLC exit 12. Other errors are harness failures.
        if run.returncode not in {0, 12} or (run.returncode == 12) != bool(failures):
            raise RuntimeError(f"TLC exited {run.returncode}: {run.stdout}")
        if not failures and not DONE.search(run.stdout):
            raise RuntimeError(f"TLC did not complete: {run.stdout}")
        return invariant in failures
    completed = DONE.search(run.stdout)
    if not completed or run.returncode not in {0, 12}:
        raise RuntimeError(f"TLC did not exhaust the reachable graph: {run.stdout}")
    paths, distinct = graph_paths(folder / "paths.dot")
    if distinct != int(completed[1]):
        raise ValueError(f"Parsed {distinct} states, expected {completed[1]}")
    for states, _ in paths:
        last = states[-1]["phase"]
        if last not in ("done", "lateCall", ("done", "done")):
            raise ValueError(f"Unexpected terminal state: {states[-1]}")
    return {"states": int(completed[1]), "failures": sorted(failures), "paths": paths}
