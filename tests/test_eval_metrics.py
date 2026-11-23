import csv
import json
from collections import Counter

import pytest

from eval import data, metrics
from eval.config import LABELS
from eval.run import main

ROWS = [(1, 0.9), (0, 0.8), (1, 0.7), (0, 0.1)]


def test_confusion_and_error_rates_include_threshold():
    counts = metrics.confusion(ROWS, 0.8)
    assert counts == metrics.Counts(1, 1, 1, 1)
    assert metrics.rates(counts) == {
        "FPR": (1, 2),
        "FNR": (1, 2),
        "Precision": (1, 2),
        "Recall": (1, 2),
    }
    assert metrics.ratio(1, 2) == 0.5
    assert metrics.ratio(0, 0) is None


def test_wilson_interval():
    assert metrics.wilson(5, 10) == pytest.approx((0.2365931, 0.7634069))
    assert metrics.wilson(0, 0) is None


def test_roc_and_pr_aucs_group_ties_and_handle_empty_classes():
    assert metrics.aucs(ROWS) == pytest.approx((0.75, 19 / 24))
    assert metrics.aucs([(0, 0.5), (1, 0.5)]) == pytest.approx((0.5, 0.75))
    assert metrics.aucs([(1, 0.1)]) == (None, 1.0)
    assert metrics.aucs([(0, 0.1)]) == (None, None)
    assert metrics.aucs([]) == (None, None)


def test_thresholds_maximize_noticeable_recall_within_budget():
    assert metrics.choose_thresholds(ROWS) == {
        "FPR ≤ 1%": 0.9,
        "FPR ≤ 5%": 0.9,
        "Best F1": 0.7,
    }
    assert metrics.choose_thresholds([(0, 1), (1, 1)])["FPR ≤ 1%"] is None


def test_brier_ece_and_reliability_boundaries():
    rows = [(0, 0.0), (0, 0.1), (1, 0.1), (1, 1.0)]
    bins = metrics.reliability(rows)
    assert len(bins) == 10
    assert bins[0] == (1, 0, 0)
    assert bins[1] == (2, 0.1, 0.5)
    assert bins[2] == (0, None, None)
    assert bins[-1] == (1, 1, 1)
    assert metrics.brier(rows) == pytest.approx(0.205)
    assert metrics.ece(rows) == pytest.approx(0.2)
    assert metrics.ece_interval([(1, 1.0)], seed=2, samples=10) == (0, 0)


def test_latency_percentiles_and_cost():
    assert metrics.latency_percentiles([0, 10, 20]) == pytest.approx((10, 19, 19.8))
    assert metrics.latency_percentiles([]) == (None, None, None)
    assert metrics.cost(1_000_000) == pytest.approx(0.03)


def test_dataset_sampling_uses_available_rows_and_has_disjoint_ids():
    intents = {f"banking{index}" for index in range(15)}
    dataset = {
        "val": [["sample", "banking0"], ["sample", "travel"]],
        "test": [["sample", "banking1"]],
        "oos_val": [["sample", "oos"]],
        "oos_test": [["sample", "oos"]],
    }
    parts = data.sample(dataset, intents)
    assert parts == data.sample(dataset, intents)
    assert Counter(row.source for row in parts["validation"]) == {
        "banking": 1,
        "other": 1,
        "oos": 1,
    }
    assert {row.id for row in parts["validation"]}.isdisjoint(
        row.id for row in parts["test"]
    )


def test_dataset_cache_rejects_a_digest_mismatch(tmp_path):
    (tmp_path / "data_full.json").write_text("{}")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        data.dataset_file("data_full.json", tmp_path)


def test_offline_eval_pipeline(tmp_path):
    results = ["--results", str(tmp_path)]
    collect = ["collect", "--fake", "--limit", "30", *results]
    assert main(collect) == 0
    csv_path = tmp_path / "drex-max-v1.1-validation.csv"
    original = csv_path.read_text()
    with csv_path.open() as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 30
    assert "text" not in rows[0]
    assert {row["source"] for row in rows} == set(LABELS)
    assert {row["latency_sample"] for row in rows} == {"sequential", "concurrent"}

    # Resume a partial CSV without repeating the completed rows.
    csv_path.write_text("\n".join(original.splitlines()[:10]) + "\n")
    assert main(collect) == 0
    assert sorted(csv_path.read_text().splitlines()) == sorted(original.splitlines())

    test_csv = tmp_path / "drex-max-v1.1-test.csv"
    held_out = test_csv.read_bytes()
    test_csv.unlink()
    assert main(["select", *results]) == 0
    selected = json.loads((tmp_path / "thresholds.json").read_text())
    assert set(selected["thresholds"]) == {"drex-max-v1.1", "drex-v2.0"}
    test_csv.write_bytes(held_out)

    assert main(["report", *results]) == 0
    report = (tmp_path / "REPORT.md").read_text()
    for item in (
        "Positive means out of scope",
        "Wilson",
        "Brier",
        "PR AUC",
        "Reliability",
        "evaluation_time_ms",
        "Up to 8 in flight",
        "Input tokens",
        "Other-domain",
    ):
        assert item in report
    assert main(["report", "--update-readme", *results]) == 1


def test_offline_report_formats_thresholds_without_rounding_computations(
    tmp_path, monkeypatch
):
    import re

    from eval import run

    # Exercise the README branch with a saved offline fixture in an isolated repository.
    root = tmp_path / "repository" / "eval"
    root.mkdir(parents=True)
    monkeypatch.setattr(run, "ROOT", root)
    readme = root.parent / "README.md"
    readme.write_text(
        "Before\n<!-- eval-table:start -->\nPending\n<!-- eval-table:end -->\nAfter\n"
    )
    results = root / "fixture"
    args = ["--results", str(results)]
    assert (
        main(["collect", "--fake", "--limit", "6", "--models", "drex-max-v1.1", *args])
        == 0
    )
    assert main(["select", *args]) == 0
    manifest_path = results / "collection.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(dataset="offline-fixture", limit=None)
    manifest_path.write_text(json.dumps(manifest))
    thresholds_path = results / "thresholds.json"
    thresholds = json.loads(thresholds_path.read_text())
    thresholds["collection"] = manifest
    thresholds["thresholds"]["drex-max-v1.1"]["Best F1"] = 0.123449999
    thresholds_path.write_text(json.dumps(thresholds))
    exact = thresholds_path.read_bytes()

    path = results / "drex-max-v1.1-test.csv"
    with path.open() as file:
        reader = csv.DictReader(file)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row["noul"] = "0.12344"
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    output = root / "REPORT.md"
    assert main(["report", "--update-readme", "--output", str(output), *args]) == 0
    report = output.read_text()
    assert "| Best F1 | 0.1234 | 0 | 0 | 2 | 4 |" in report
    assert "| Baseline | 0.5000 |" in report
    assert "| 0.0500 |" in report
    assert (
        "four decimal places" in report
        and "exact values from thresholds.json" in report
    )
    assert "| drex-max-v1.1 | Best F1 | 0.1234 |" in readme.read_text()
    assert "| drex-max-v1.1 | Baseline | 0.5000 |" in readme.read_text()
    assert "0.123449999" not in report + readme.read_text()
    assert re.search(r"Before\n<!-- eval-table:start -->", readme.read_text())
    assert thresholds_path.read_bytes() == exact
