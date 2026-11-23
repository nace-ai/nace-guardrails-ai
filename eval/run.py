"""Collect scores, select validation thresholds, and report held-out results."""

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter_ns

from . import data, metrics
from .config import CRITERIA, LABELS, MODELS, QUESTION, SEED

ROOT = Path(__file__).resolve().parent
FIELDS = (
    "row_id",
    "label",
    "source",
    "noul",
    "response_model",
    "input_tokens",
    "client_latency_ms",
    "evaluation_time_ms",
    "latency_sample",
)


def positive_int(value):
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return parsed


def model_ids(value):
    models = value.split(",")
    if len(set(models)) != len(models) or not all(
        re.fullmatch(r"[a-z0-9][a-z0-9.-]*", m) for m in models
    ):
        raise argparse.ArgumentTypeError("Use distinct model IDs separated by commas.")
    return models


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    temporary.replace(path)


def setup_collection(args, partitions):
    directory = args.results
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset": "synthetic" if args.fake else data.COMMIT,
        "seed": SEED,
        "question": QUESTION,
        "criteria": CRITERIA,
        "models": list(args.models),
        "concurrency": args.concurrency,
        "limit": args.limit,
        "row_ids": {
            part: [row.id for row in rows] for part, rows in partitions.items()
        },
    }
    path = directory / "collection.json"
    if path.exists():
        previous = json.loads(path.read_text())
        if any(previous.get(key) != value for key, value in manifest.items()):
            raise ValueError(
                "Collection settings changed. Use a new results directory."
            )
        return previous
    manifest["date"] = datetime.now(timezone.utc).date().isoformat()
    write_json(path, manifest)
    return manifest


def read_scores(path):
    with path.open(newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != list(FIELDS):
            raise ValueError(f"Unexpected CSV header in {path.name}.")
        rows = {}
        for row in reader:
            if (
                not row["row_id"]
                or row["row_id"] in rows
                or any(row[key] is None for key in FIELDS)
                or row["source"] not in LABELS
                or int(row["label"]) != LABELS[row["source"]]
                or not 0 <= float(row["noul"]) <= 1
                or row["latency_sample"] not in {"sequential", "concurrent"}
            ):
                raise ValueError(f"Invalid or duplicate score row in {path.name}.")
            rows[row["row_id"]] = row
        return rows


def fake_score(model, row):
    digest = hashlib.sha256(f"{model}:{row.id}".encode()).digest()
    noise = int.from_bytes(digest[:2], "big") / 65535
    probability = 0.65 * row.label + noise * 0.35
    return probability, model, 10 + digest[2], 3 + digest[3], 1 + digest[4]


async def live_score(client, question, model, row):
    from nace_sdk import NoulAnswer

    start = perf_counter_ns()
    response = await client.system_one(row.text, {"scope": question}, model=model)
    latency = (perf_counter_ns() - start) / 1_000_000
    answer = response.answers.get("scope")
    if (
        not isinstance(answer, NoulAnswer)
        or not math.isfinite(answer.noul)
        or not 0 <= answer.noul <= 1
    ):
        raise ValueError("Drex returned an unusable noul.")
    return (
        answer.noul,
        response.model,
        response.usage.input_tokens,
        latency,
        response.evaluation_time_ms,
    )


async def collect_part(path, model, rows, concurrency, scorer):
    existing = read_scores(path) if path.exists() else {}
    if set(existing) - {row.id for row in rows}:
        raise ValueError(f"Unexpected saved row IDs in {path.name}.")
    sequential_count = min(30, max(1, len(rows) // 10))
    slots = asyncio.Semaphore(concurrency)

    async def score(row, sample):
        async with slots:
            (
                probability,
                response_model,
                tokens,
                latency,
                evaluation_time,
            ) = await scorer(model, row)
        return (
            row.id,
            row.label,
            row.source,
            probability,
            response_model,
            tokens,
            latency,
            evaluation_time,
            sample,
        )

    with path.open("a", newline="") as file:
        writer = csv.writer(file)
        if file.tell() == 0:
            writer.writerow(FIELDS)
            file.flush()

        def append(result):
            writer.writerow(result)
            file.flush()

        for row in rows[:sequential_count]:
            if row.id not in existing:
                append(await score(row, "sequential"))
        tasks = [
            asyncio.create_task(score(row, "concurrent"))
            for row in rows[sequential_count:]
            if row.id not in existing
        ]
        try:
            for task in asyncio.as_completed(tasks):
                append(await task)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def collect(args):
    key = os.environ.get("NACE_API_KEY") if not args.fake else None
    if not args.fake and not key:
        raise ValueError("Set NACE_API_KEY to collect live scores.")
    partitions = {
        part: rows[: args.limit]
        for part, rows in (data.synthetic() if args.fake else data.load()).items()
    }
    setup_collection(args, partitions)

    async def run(scorer):
        for model in args.models:
            for part, rows in partitions.items():
                counts = Counter(row.source for row in rows)
                print(
                    f"{model} {part} "
                    + ", ".join(f"{source}={counts[source]}" for source in LABELS)
                )
                await collect_part(
                    args.results / f"{model}-{part}.csv",
                    model,
                    rows,
                    args.concurrency,
                    scorer,
                )

    if args.fake:

        async def deterministic(model, row):
            return fake_score(model, row)

        await run(deterministic)
    else:
        from nace_sdk import AsyncNaceClient, NaceError, Noul, RetryPolicy

        question = Noul(instructions=QUESTION, criteria=CRITERIA)
        try:
            async with AsyncNaceClient(
                api_key=key, timeout=60, retry=RetryPolicy(timeout=180)
            ) as client:

                async def request(model, row):
                    return await live_score(client, question, model, row)

                await run(request)
        except NaceError as error:
            raise ValueError(
                f"Collection stopped with {type(error).__name__}. Rerun to resume."
            ) from None


def complete_scores(directory, manifest, model, part):
    path = directory / f"{model}-{part}.csv"
    rows = read_scores(path)
    expected = manifest["row_ids"][part]
    if set(rows) != set(expected):
        raise ValueError(f"{path.name} is incomplete. Resume collection first.")
    return [rows[row_id] for row_id in expected]


def pairs(rows):
    return [(int(row["label"]), float(row["noul"])) for row in rows]


def select(args):
    manifest = json.loads((args.results / "collection.json").read_text())
    thresholds = {}
    for model in manifest["models"]:
        rows = complete_scores(args.results, manifest, model, "validation")
        thresholds[model] = metrics.choose_thresholds(pairs(rows))
    write_json(
        args.results / "thresholds.json",
        {"collection": manifest, "thresholds": thresholds},
    )


def proportion(value):
    return "Unavailable" if value is None else f"{value:.2%}"


def number(value):
    return "Unavailable" if value is None else f"{value:.4f}"


def interval(values):
    return (
        "Unavailable"
        if values is None
        else f"{proportion(values[0])} to {proportion(values[1])}"
    )


def rate_cell(successes, size):
    value = metrics.ratio(successes, size)
    return (
        "Unavailable"
        if value is None
        else (f"{proportion(value)} ({interval(metrics.wilson(successes, size))})")
    )


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(map(str, row)) + " |" for row in rows),
    ]


def threshold_table(rows, thresholds):
    lines = []
    for name, threshold in thresholds.items():
        if threshold is None:
            lines.append([name, *["Unavailable"] * 9])
            continue
        counts = metrics.confusion(rows, threshold)
        lines.append(
            [
                name,
                f"{threshold:.4f}",
                counts.tp,
                counts.fp,
                counts.tn,
                counts.fn,
                *(rate_cell(k, n) for k, n in metrics.rates(counts).values()),
            ]
        )
    return table(
        [
            "Selection",
            "fail_at",
            "TP",
            "FP",
            "TN",
            "FN",
            "FPR",
            "FNR",
            "Precision",
            "Recall",
        ],
        lines,
    )


def error_sweep(rows):
    values = []
    for step in range(1, 20):
        threshold = step / 20
        rates = metrics.rates(metrics.confusion(rows, threshold))
        values.append(
            [
                f"{threshold:.4f}",
                *(proportion(metrics.ratio(*rates[rate])) for rate in ("FPR", "FNR")),
            ]
        )
    return table(["fail_at", "FPR", "FNR"], values)


def latency_table(rows, concurrency):
    values = []
    for sample, title in (
        ("sequential", "Sequential"),
        ("concurrent", f"Up to {concurrency} in flight"),
    ):
        for field in ("client_latency_ms", "evaluation_time_ms"):
            timings = [
                float(row[field])
                for row in rows
                if row["latency_sample"] == sample and row[field]
            ]
            values.append(
                [
                    title,
                    field,
                    len(timings),
                    *map(number, metrics.latency_percentiles(timings)),
                ]
            )
    return table(["Sample", "Measurement", "N", "p50 ms", "p95 ms", "p99 ms"], values)


def cost_table(rows):
    values = []
    for model in sorted({row["response_model"] for row in rows}):
        requests = [row for row in rows if row["response_model"] == model]
        counts = [int(row["input_tokens"]) for row in requests if row["input_tokens"]]
        costs = (
            f"${metrics.cost(sum(counts)):.8f}"
            if model in MODELS and counts
            else "Unavailable"
        )
        values.append([model, len(requests), len(counts), sum(counts), costs])
    return table(
        [
            "Response model",
            "Requests",
            "Requests with usage",
            "Input tokens",
            "Cost for reported tokens",
        ],
        values,
    )


def model_report(model, rows, thresholds, concurrency):
    measured = pairs(rows)
    roc, pr = metrics.aucs(measured)
    ece = metrics.ece(measured)
    ece_ci = metrics.ece_interval(measured, seed=SEED)
    counts = Counter(row["source"] for row in rows)
    lines = [
        f"## {model}",
        "",
        ", ".join(f"{key} {counts[key]}" for key in LABELS) + ".",
        "",
        f"ROC AUC {number(roc)}. PR AUC {number(pr)} (trapezoidal).",
        f"Brier score {number(metrics.brier(measured))}.",
        f"ECE {proportion(ece)} (95% bootstrap interval {interval(ece_ci)}).",
        "",
        "### Held-out thresholds",
        "",
        *threshold_table(measured, thresholds),
        "",
    ]
    for source, title in (("oos", "CLINC150 out-of-scope"), ("other", "Other-domain")):
        subset = pairs([row for row in rows if row["source"] in {"banking", source}])
        lines.extend(
            [
                f"### {title} compared with banking",
                "",
                *threshold_table(subset, thresholds),
                "",
            ]
        )
    lines.extend(["### Error rates by threshold", "", *error_sweep(measured), ""])
    reliability = [
        (
            f"{index / 10:.1f} to {(index + 1) / 10:.1f}",
            count,
            proportion(prediction),
            proportion(observed),
        )
        for index, (count, prediction, observed) in enumerate(
            metrics.reliability(measured)
        )
    ]
    lines.extend(
        [
            "### Reliability",
            "",
            "The bins include their lower endpoint. The final bin also includes 1.",
            "",
            *table(["Bin", "N", "Mean P(yes)", "Observed positive rate"], reliability),
            "",
            "### Latency and token use",
            "",
            *latency_table(rows, concurrency),
            "",
            *cost_table(rows),
            "",
            f"See `{model}-test.csv` in the results directory for token usage per request.",
            "",
        ]
    )
    return lines, ece, ece_ci


def update_readme(path, summary):
    start, end = "<!-- eval-table:start -->", "<!-- eval-table:end -->"
    contents = path.read_text()
    if (
        contents.count(start) != 1
        or contents.count(end) != 1
        or contents.index(start) > contents.index(end)
    ):
        raise ValueError(
            "README must contain one ordered evaluation table marker pair."
        )
    before, rest = contents.split(start, 1)
    _, after = rest.split(end, 1)
    path.write_text(before + start + "\n" + "\n".join(summary) + "\n" + end + after)


def report(args):
    manifest = json.loads((args.results / "collection.json").read_text())
    selection = json.loads((args.results / "thresholds.json").read_text())
    if selection["collection"] != manifest:
        raise ValueError("Thresholds belong to a different collection.")
    fake = manifest["dataset"] == "synthetic"
    if args.update_readme and (fake or manifest["limit"] is not None):
        raise ValueError("README guidance requires a complete live evaluation.")
    dataset = "Synthetic offline fixture" if fake else "CLINC150"
    collected = date.fromisoformat(manifest["date"]).isoformat()
    lines = [
        "# Banking support threshold evaluation",
        "",
        f"{dataset}. Collection started {collected}.",
        "",
        f"Question: {manifest['question']}",
        "",
        "Positive means out of scope. A positive decision fails the guard.",
        "Thresholds were selected on validation and applied to the held-out test split.",
        "Tables display fail_at to four decimal places while computations use the exact values from thresholds.json.",
        "Intervals for proportions use the 95% Wilson method.",
        "",
    ]
    summary = []
    for model in manifest["models"]:
        rows = complete_scores(args.results, manifest, model, "test")
        thresholds = selection["thresholds"][model] | {"Baseline": 0.5}
        sections, ece, ece_ci = model_report(
            model, rows, thresholds, manifest["concurrency"]
        )
        lines.extend(sections)
        for name, value in thresholds.items():
            if value is None:
                summary.append(
                    [
                        model,
                        name,
                        "Unavailable",
                        "Unavailable",
                        "Unavailable",
                        proportion(ece),
                        interval(ece_ci),
                    ]
                )
                continue
            rates = metrics.rates(metrics.confusion(pairs(rows), value))
            summary.append(
                [
                    model,
                    name,
                    f"{value:.4f}",
                    *(rate_cell(*rates[rate]) for rate in ("FPR", "FNR")),
                    proportion(ece),
                    interval(ece_ci),
                ]
            )
    output = args.output or (args.results / "REPORT.md" if fake else ROOT / "REPORT.md")
    output.write_text("\n".join(lines))
    if args.update_readme:
        update_readme(
            ROOT.parent / "README.md",
            [
                f"{dataset}, collection started {collected}. These results apply to this dataset and this question.",
                "",
                *table(
                    [
                        "Model",
                        "Selection",
                        "fail_at",
                        "Test FPR (95% CI)",
                        "Test FNR (95% CI)",
                        "ECE",
                        "ECE 95% interval",
                    ],
                    summary,
                ),
            ],
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "select", "report"):
        command = commands.add_parser(name)
        command.add_argument("--results", type=Path, default=ROOT / "results")
        if name == "collect":
            command.add_argument("--models", type=model_ids, default=MODELS)
            command.add_argument("--concurrency", type=positive_int, default=8)
            command.add_argument("--limit", type=positive_int)
            command.add_argument("--fake", action="store_true")
        if name == "report":
            command.add_argument("--output", type=Path)
            command.add_argument("--update-readme", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            asyncio.run(collect(args))
        elif args.command == "select":
            select(args)
        else:
            report(args)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
