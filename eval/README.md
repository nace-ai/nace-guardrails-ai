# Measure a banking support threshold

This evaluation uses a fixed question and rubric in [config.py](config.py). Positive means out of scope. A positive decision fails the guard.

The data comes from CLINC150 by Larson and colleagues, EMNLP 2019. The two source files are pinned to a commit and checked against their SHA-256 digests before use. The dataset license is CC BY 3.0. The cache stays in `eval/.cache/`. [Dataset and license](https://github.com/clinc/oos-eval/tree/828f8093932c8fe6ca7936c3d2e52903b1c523de)

| Split | Banking | CLINC150 out of scope | Other domains |
| --- | --- | --- | --- |
| Validation | 300 | 100 | 300 |
| Test | 450 | 1000 | 450 |

The banking intents come from the pinned domain file. Sampling uses a fixed seed. If a split has fewer rows than requested, collection uses the available rows and prints the counts.

## Collect live results

Install `drex-policy` in your environment. Set `NACE_API_KEY`, then run these commands from the repository root.

```sh
make eval ARGS='collect --models drex-max-v1.1,drex-v2.0 --concurrency 8'
make eval ARGS='select'
make eval ARGS='report'
```

Collection writes a CSV for each model and split under `eval/results/`. It records raw scores, response model, token usage, and latency. The CSV contains row IDs and labels. The text stays in the local dataset cache. One subset runs sequentially. The rest uses the concurrency you set. Rerun with the same arguments to resume. Use `--limit N` and a separate `--results PATH` for a short trial.

`select` reads validation data and records thresholds for up to 1% and 5% false positives and for maximum F1. If no threshold meets a target, the report marks it unavailable. `report` applies the saved thresholds and the 0.5 baseline to the test data. It writes `eval/REPORT.md`. Use `report --update-readme` to insert the measured table in the README after a complete live run.

## Run offline

Use a separate results directory for the synthetic fixture. No network or key is needed.

```sh
make eval ARGS='collect --fake --limit 30 --results eval/results/fake'
make eval ARGS='select --results eval/results/fake'
make eval ARGS='report --results eval/results/fake'
```

Read the synthetic report in `eval/results/fake/REPORT.md`.
