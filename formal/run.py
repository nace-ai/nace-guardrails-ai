"""Run the offline models, replay, generated checks, and mutation checks."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path

from model import BASELINES, MODEL_MUTANTS, tlc
from mutants import SOURCE, source_for

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
JAR = HERE / ".tools" / "tla2tools-1.7.4.jar"
JAR_SHA = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
PROPERTIES = [f"P{i}" for i in range(1, 11)]
BASE_CHECKS = {
    "P1": ("p1_original_equals_fix", "p1_replacement_decisions"),
    "P2": ("p2_values", "p2_wire", "p2_masked_answer", "p2_response_validation"),
    "P4": ("p4",),
    "P5": ("p5",),
    "P7": ("p7_valid", "p7_bad", "p7_request_encoding", "p7_data_url_payload"),
    "P9": ("p9_results", "p9_chain"),
    "P10": ("p10_message", "p10_body_request_id", "p10_request_ids"),
}
MUTANTS = {
    "duplicate_fail": ("P1", "validation"),
    "pass_after_fail": ("P1", "validation"),
    "no_finite": ("P2", "p2_values"),
    "map_401": ("P2", "p2_wire"),
    "catch_cancel": ("P3", "validation"),
    "catch_cancel_as_availability": ("P3", "validation"),
    "remove_cancel_flag": ("P3", "validation"),
    "remove_replacement_check": ("P1", "validation"),
    "allow_error_replacement": ("P1", "validation"),
    "unchecked_data_url": ("P7", "p7_bad"),
    "masked_availability": ("P2", "p2_masked_answer"),
    "unchecked_mapping": ("P7", "p7_request_encoding"),
    "unchecked_request_id": ("P10", "p10_body_request_id"),
    "strict_threshold": ("P4", "p4"),
    "swap_branches": ("P4", "p4"),
    "round_probability": ("P5", "p5"),
    "drop_answer_key": ("P5", "p5"),
    "requested_as_response": ("P5", "p5"),
    "double_call": ("P6", "validation"),
    "drop_text_only_images": ("P7", "p7_valid"),
    "shared_answer": ("P8", "concurrency"),
    "drop_caller_metadata": ("P9", "p9_results"),
    "copy_error_text": ("P10", "p10_message"),
}


def offline_imports(mutant):
    sys.path.insert(0, str(ROOT))
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ.pop("NACE_API_KEY", None)

    def audit(event, args):
        if event in {
            "socket.connect",
            "socket.getaddrinfo",
            "socket.gethostbyaddr",
            "socket.gethostbyname",
            "socket.getnameinfo",
            "socket.sendto",
        }:
            raise RuntimeError(f"Offline verification blocked {event}")

    sys.addaudithook(audit)
    from guardrails.settings import settings

    import drex_policy
    from drex_policy import main

    settings.rc.enable_metrics = False
    source = source_for(mutant)
    if source != SOURCE.read_text():
        exec(compile(source, f"<{mutant}:drex_policy/main.py>", "exec"), vars(main))  # noqa: S102
        drex_policy.DrexPolicy = main.DrexPolicy
        drex_policy.DrexPolicyError = main.DrexPolicyError
    main._FORMAL_SOURCE = source
    main._FORMAL_MUTANT = mutant


def select_paths(paths, mutant):
    if mutant == "baseline":
        return paths
    if mutant in {"duplicate_fail", "pass_after_fail", "double_call"}:
        action = "noop" if mutant == "double_call" else "custom"
        return [
            (states, steps)
            for states, steps in paths
            if states[0]["route"] in {"sync", "native"}
            and states[0]["reply"] == "fail"
            and states[0]["action"] == action
            and states[0]["mode"] == "raise"
            and not states[-1]["cancelled"]
        ]
    if mutant == "remove_cancel_flag":
        return [
            (states, steps)
            for states, steps in paths
            if states[0]["route"] == "executor"
            and states[0]["action"] == "noop"
            and states[0]["reply"] == "pass"
            and states[0]["mode"] == "raise"
            and "CancelExecutorBeforeCall" in steps
        ]
    if mutant == "remove_replacement_check":
        return [
            (states, steps)
            for states, steps in paths
            if states[0]["action"] == "fix_reask"
            and states[0]["reply"] == "fail"
            and states[0]["retry"] == "fail"
            and states[0]["mode"] == "raise"
            and not states[-1]["cancelled"]
        ]
    if mutant == "allow_error_replacement":
        return [
            (states, steps)
            for states, steps in paths
            if states[0]["action"] == "fix_reask"
            and states[0]["reply"] == "fail"
            and states[0]["retry"] == "availability"
            and states[0]["mode"] == "pass"
            and not states[-1]["cancelled"]
        ]
    return [
        (states, steps)
        for states, steps in paths
        if states[0]["route"] == "native"
        and states[0]["action"] == "noop"
        and states[0]["mode"] == "pass"
        and states[0]["reply"] == "pass"
        and steps == ["CallDrex", "CancelInFlight"]
    ]


def worker(output, mutant, *, control=False):
    offline_imports("baseline" if control else mutant)
    import generated
    from replay import (
        concurrency_case,
        p3_native_wire,
        p6_non_string,
        validation_violations,
    )
    from support import Violation

    failures = defaultdict(list)
    counts = {}
    baseline = mutant == "baseline"
    property_id, check = MUTANTS.get(mutant, (None, None))

    def captures(property_id, check, invoke, *args):
        try:
            invoke(*args)
        except Violation as failure:
            failures[property_id].append({"check": check, "witness": str(failure)})
            traceback.print_exc()

    if baseline or check == "validation":
        all_paths = json.loads((output / "Validation.paths.json").read_text())
        paths = select_paths(all_paths, mutant)
        if not paths:
            raise ValueError(f"No validation paths for {mutant}")
        counts["Validation"] = len(paths)
        for path in paths:
            for name, witness in validation_violations(
                path, duplicate_fail=mutant == "duplicate_fail" and not control
            ):
                if baseline or name == property_id:
                    failures[name].append({"check": "validation", "witness": witness})
    if baseline or check == "concurrency":
        paths = json.loads((output / "Concurrency.paths.json").read_text())
        counts["Concurrency"] = len(paths)
        for states, steps in paths:
            for state in states:
                state["phase"] = tuple(state["phase"])
            captures("P8", "concurrency", concurrency_case, (states, steps))
    for name, checks in BASE_CHECKS.items():
        for function in checks:
            if (
                baseline
                or (name, function) == (property_id, check)
                or mutant == "drop_caller_metadata"
                and function == "p9_chain"
            ):
                captures(name, function, getattr(generated, function))
    if baseline or mutant in {"catch_cancel", "catch_cancel_as_availability"}:
        captures("P3", "native HTTP cancellation", p3_native_wire)
    if baseline:
        captures("P6", "non-string refusals", p6_non_string)
    return {"failures": failures, "paths": counts}


def run_worker(output, mutant, *, control=False):
    label = f"{mutant}.control" if control else mutant
    args = [sys.executable, str(__file__), "--output", str(output), "--worker", mutant]
    if control:
        args.append("--control")
    with (output / f"{label}.log").open("w") as log:
        run = subprocess.run(
            args,
            check=False,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    result = output / f"{label}.json"
    if run.returncode or not result.is_file():
        raise RuntimeError(f"{label} worker exited {run.returncode}. See {log.name}")
    return json.loads(result.read_text())


def check_artifact_visibility():
    caches = {".tools", "__pycache__", ".hypothesis", ".pytest_cache", ".ruff_cache"}
    artifacts = [
        str(path.relative_to(ROOT))
        for path in HERE.rglob("*")
        if path.is_file() and not caches.intersection(path.relative_to(HERE).parts)
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        input="\n".join(artifacts) + "\n",
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 1:
        raise RuntimeError(
            f"Formal artifacts are ignored or git failed: {result.stdout}{result.stderr}"
        )
    return len(artifacts)


def check(output):
    print(f"Evidence directory: {output}", flush=True)
    if not JAR.is_file() or hashlib.sha256(JAR.read_bytes()).hexdigest() != JAR_SHA:
        raise SystemExit("Pinned TLC jar missing or changed. See formal/SKILL.md.")
    versions = {
        package: version(package)
        for package in ("guardrails-ai", "nace-sdk", "hypothesis")
    }
    visible_artifacts = check_artifact_visibility()
    print("Replay versions:", versions, flush=True)
    print(
        "Validator SHA-256:",
        hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        flush=True,
    )
    models = {}
    for module in BASELINES:
        data = tlc(JAR, output, module)
        (output / f"{module}.paths.json").write_text(
            json.dumps(data.pop("paths")) + "\n"
        )
        models[module] = data
        print(
            f"{module}: {data['states']} states, violations: {data['failures']}",
            flush=True,
        )
    baseline = run_worker(output, "baseline")
    print(f"Replayed paths: {baseline['paths']}", flush=True)
    for prop, failures in baseline["failures"].items():
        first_for_check = {}
        for failure in failures:
            first_for_check.setdefault(failure["check"], failure["witness"])
        for name, witness in first_for_check.items():
            print(f"{prop} {name}: {witness}", flush=True)

    mutations = defaultdict(dict)
    for name, (property_id, check_name) in MUTANTS.items():
        worker_result = run_worker(output, name)
        killed_in_code = bool(worker_result["failures"].get(property_id))
        if killed_in_code:
            control = run_worker(output, name, control=True)
            if control["failures"].get(property_id):
                raise RuntimeError(f"Mutation check {name} also fails on the original")
        killed_in_tlc = None
        if name in MODEL_MUTANTS:
            module, variant, invariant = MODEL_MUTANTS[name]
            killed_in_tlc = tlc(
                JAR, output, module, variant=variant, invariant=invariant, name=name
            )
        killed = killed_in_code and killed_in_tlc is not False
        mutations[property_id][name] = {
            "killed": killed,
            "replay_or_generated": killed_in_code,
            "tlc": killed_in_tlc,
            "check": check_name,
        }
        print(f"{property_id} {name}: {'killed' if killed else 'survived'}", flush=True)

    failed_model = {
        re.match(r"P\d+", failure)[0]
        for model in models.values()
        for failure in model["failures"]
    }
    summary = {}
    for prop in PROPERTIES:
        holds = prop not in failed_model and prop not in baseline["failures"]
        killed = all(item["killed"] for item in mutations[prop].values())
        summary[prop] = {
            "pass": holds and killed,
            "baseline_holds": holds,
            "mutants_killed": killed,
        }
        print(
            f"{prop}: {'PASS' if holds and killed else 'FAIL'}, mutant killed: {'yes' if killed else 'no'}"
        )
    (output / "summary.json").write_text(
        json.dumps(
            {
                "models": models,
                "baseline": baseline,
                "mutants": mutations,
                "summary": summary,
            },
            indent=2,
        )
        + "\n"
    )
    evidence = {
        "sync_commit": "babb3e5 plus uncommitted changes",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "versions": versions,
        "models": models,
        "paths": baseline["paths"],
        "summary": summary,
        "mutants": mutations,
        "counterexamples": {
            name: failures[0] for name, failures in baseline["failures"].items()
        },
        "visible_formal_artifacts": visible_artifacts,
        "thread_cancellation_boundary": "The flag read immediately before system_one is the request commit point. Cancellation delivered after it cannot stop the sync request.",
        "evidence_directory": str(output),
    }
    (HERE / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return 0 if all(result["pass"] for result in summary.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="New directory for TLC and replay evidence"
    )
    parser.add_argument(
        "--worker", choices=("baseline", *MUTANTS), help=argparse.SUPPRESS
    )
    parser.add_argument("--control", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        label = f"{args.worker}.control" if args.control else args.worker
        record = worker(args.output, args.worker, control=args.control)
        (args.output / f"{label}.json").write_text(json.dumps(record, indent=2) + "\n")
        return 0
    if args.output:
        output = args.output.resolve()
        output.mkdir()
    else:
        output = Path(tempfile.mkdtemp(prefix="drex-formal-"))
    return check(output)


if __name__ == "__main__":
    raise SystemExit(main())
