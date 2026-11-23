"""Pure metrics for probabilities of an out-of-scope message."""

import math
import random
from dataclasses import dataclass
from itertools import groupby
from statistics import NormalDist


@dataclass(frozen=True)
class Counts:
    tp: int
    fp: int
    tn: int
    fn: int


def confusion(rows, threshold):
    tp = fp = tn = fn = 0
    for label, score in rows:
        if score >= threshold:
            tp += label
            fp += 1 - label
        else:
            fn += label
            tn += 1 - label
    return Counts(tp, fp, tn, fn)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def rates(counts):
    return {
        "FPR": (counts.fp, counts.fp + counts.tn),
        "FNR": (counts.fn, counts.fn + counts.tp),
        "Precision": (counts.tp, counts.tp + counts.fp),
        "Recall": (counts.tp, counts.tp + counts.fn),
    }


def wilson(successes, size, confidence=0.95):
    if not size:
        return None
    p = successes / size
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    denominator = 1 + z**2 / size
    center = (p + z**2 / (2 * size)) / denominator
    half = z * math.sqrt((p * (1 - p) + z**2 / (4 * size)) / size) / denominator
    return max(0, center - half), min(1, center + half)


def threshold_curve(rows):
    """Yield each distinct score and its counts, with tied scores grouped."""
    ordered = sorted(rows, key=lambda row: row[1], reverse=True)
    positives = sum(label for label, _ in ordered)
    negatives = len(ordered) - positives
    tp = fp = 0
    for threshold, tied in groupby(ordered, key=lambda row: row[1]):
        for label, _ in tied:
            tp += label
            fp += 1 - label
        yield threshold, Counts(tp, fp, negatives - fp, positives - tp)


def choose_thresholds(rows):
    if len({label for label, _ in rows}) != 2:
        raise ValueError("Threshold selection requires both labels.")
    candidates = dict(threshold_curve(rows))
    candidates[1.0] = confusion(rows, 1.0)

    def rank(candidate):
        threshold, counts = candidate
        f1 = ratio(2 * counts.tp, 2 * counts.tp + counts.fp + counts.fn)
        return f1, -counts.fp, counts.tp, threshold

    selected = {}
    for target in (1, 5):
        eligible = [
            (threshold, counts)
            for threshold, counts in candidates.items()
            if 100 * counts.fp <= target * (counts.fp + counts.tn)
        ]
        best = max(
            eligible, key=lambda item: (item[1].tp, -item[1].fp, item[0]), default=None
        )
        selected[f"FPR ≤ {target}%"] = best[0] if best else None
    selected["Best F1"] = max(candidates.items(), key=rank)[0]
    return selected


def aucs(rows):
    """Trapezoidal ROC and PR areas, with the PR curve starting at (0, 1)."""
    positives = sum(label for label, _ in rows)
    negatives = len(rows) - positives
    if not positives:
        return None, None
    if not negatives:
        return None, 1.0
    old_fpr, old_recall, old_precision = 0.0, 0.0, 1.0
    roc = pr = 0.0
    for _, counts in threshold_curve(rows):
        fpr = counts.fp / negatives
        recall = counts.tp / positives
        precision = counts.tp / (counts.tp + counts.fp)
        roc += (fpr - old_fpr) * (recall + old_recall) / 2
        pr += (recall - old_recall) * (precision + old_precision) / 2
        old_fpr, old_recall, old_precision = fpr, recall, precision
    return roc, pr


def brier(rows):
    return ratio(sum((score - label) ** 2 for label, score in rows), len(rows))


def _bin(score, bins):
    return min(int(score * bins), bins - 1)


def reliability(rows, bins=10):
    groups = [[] for _ in range(bins)]
    for label, score in rows:
        groups[_bin(score, bins)].append((label, score))
    return [
        (
            len(group),
            ratio(sum(score for _, score in group), len(group)),
            ratio(sum(label for label, _ in group), len(group)),
        )
        for group in groups
    ]


def ece(rows, bins=10):
    error = sum(
        count * abs(mean_score - fraction_positive)
        for count, mean_score, fraction_positive in reliability(rows, bins)
        if count
    )
    return ratio(error, len(rows))


def ece_interval(rows, *, seed, samples=1000):
    if not rows:
        return None
    rng = random.Random(seed)
    values = [ece(rng.choices(rows, k=len(rows))) for _ in range(samples)]
    return percentile(values, 0.025), percentile(values, 0.975)


def percentile(values, quantile):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * quantile
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def latency_percentiles(values):
    return tuple(percentile(values, q) for q in (0.5, 0.95, 0.99))


def cost(input_tokens):
    return input_tokens * 0.03 / 1_000_000
