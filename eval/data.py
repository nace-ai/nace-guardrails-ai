"""Verify and sample the CLINC150 validation and test splits."""

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from .config import LABELS, PARTS, SEED, TARGET_COUNTS

COMMIT = "828f8093932c8fe6ca7936c3d2e52903b1c523de"
BASE_URL = f"https://raw.githubusercontent.com/clinc/oos-eval/{COMMIT}/data"
HASHES = {
    "data_full.json": "36923c3705a59e08fe9c3883d8bc2dd966ef93e22cb78ac41171782a698d56e0",
    "domains.json": "b947b579d3b8e74b06f93b01083d8efaff2888b43a3e362533bd88a6e1211b3a",
}
CACHE = Path(__file__).resolve().parent / ".cache"


@dataclass(frozen=True)
class Row:
    id: str
    source: str
    text: str

    @property
    def label(self):
        return LABELS[self.source]


def dataset_file(name, cache):
    path = cache / name
    exists = path.exists()
    if exists:
        contents = path.read_bytes()
    else:
        with urlopen(f"{BASE_URL}/{name}", timeout=30) as response:
            contents = response.read()
    if hashlib.sha256(contents).hexdigest() != HASHES[name]:
        raise ValueError(f"SHA-256 mismatch for {name}")
    if not exists:
        cache.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(contents)
        temporary.replace(path)
    return json.loads(contents)


def sample(data, banking):
    if len(banking) != 15:
        raise ValueError("CLINC150 must define 15 banking intents.")
    partitions = {}
    for part in PARTS:
        rng = random.Random(f"{SEED}:{part}")
        split = {"validation": "val", "test": "test"}[part]
        pools = {source: [] for source in LABELS}
        for key in (split, f"oos_{split}"):
            for index, (text, intent) in enumerate(data[key]):
                source = (
                    "banking"
                    if intent in banking
                    else "oos"
                    if intent == "oos"
                    else "other"
                )
                pools[source].append(Row(f"{key}-{index}", source, text))
        rows = []
        for source, pool in pools.items():
            rows.extend(rng.sample(pool, min(len(pool), TARGET_COUNTS[part][source])))
        rng.shuffle(rows)
        partitions[part] = rows
    return partitions


def load(cache=CACHE):
    return sample(
        dataset_file("data_full.json", cache),
        set(dataset_file("domains.json", cache)["banking"]),
    )


def synthetic():
    """Small independent fixture for an offline pipeline run."""
    return {
        part: [
            Row(f"synthetic-{part}-{index}", source, "")
            for index, source in enumerate(list(LABELS) * 20)
        ]
        for part in PARTS
    }
