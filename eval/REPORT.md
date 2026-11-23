# Banking support threshold evaluation

CLINC150. Collection started 2026-11-23.

Question: Is this message outside the scope of a banking support assistant?

Positive means out of scope. A positive decision fails the guard.
Thresholds were selected on validation and applied to the held-out test split.
Tables display fail_at to four decimal places while computations use the exact values from thresholds.json.
Intervals for proportions use the 95% Wilson method.

## drex-max-v1.1

banking 450, oos 1000, other 450.

ROC AUC 0.9630. PR AUC 0.9898 (trapezoidal).
Brier score 0.0644.
ECE 5.52% (95% bootstrap interval 4.66% to 6.78%).

### Held-out thresholds

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.6429 | 1330 | 24 | 426 | 120 | 5.33% (3.61% to 7.81%) | 8.28% (6.97% to 9.81%) | 98.23% (97.38% to 98.81%) | 91.72% (90.19% to 93.03%) |
| FPR ≤ 1% | 0.8164 | 1297 | 5 | 445 | 153 | 1.11% (0.48% to 2.57%) | 10.55% (9.07% to 12.24%) | 99.62% (99.10% to 99.84%) | 89.45% (87.76% to 90.93%) |
| FPR ≤ 5% | 0.6429 | 1330 | 24 | 426 | 120 | 5.33% (3.61% to 7.81%) | 8.28% (6.97% to 9.81%) | 98.23% (97.38% to 98.81%) | 91.72% (90.19% to 93.03%) |
| Baseline | 0.5000 | 1343 | 44 | 406 | 107 | 9.78% (7.36% to 12.87%) | 7.38% (6.14% to 8.84%) | 96.83% (95.77% to 97.63%) | 92.62% (91.16% to 93.86%) |

### CLINC150 out-of-scope compared with banking

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.6429 | 955 | 24 | 426 | 45 | 5.33% (3.61% to 7.81%) | 4.50% (3.38% to 5.97%) | 97.55% (96.38% to 98.35%) | 95.50% (94.03% to 96.62%) |
| FPR ≤ 1% | 0.8164 | 943 | 5 | 445 | 57 | 1.11% (0.48% to 2.57%) | 5.70% (4.43% to 7.31%) | 99.47% (98.77% to 99.77%) | 94.30% (92.69% to 95.57%) |
| FPR ≤ 5% | 0.6429 | 955 | 24 | 426 | 45 | 5.33% (3.61% to 7.81%) | 4.50% (3.38% to 5.97%) | 97.55% (96.38% to 98.35%) | 95.50% (94.03% to 96.62%) |
| Baseline | 0.5000 | 960 | 44 | 406 | 40 | 9.78% (7.36% to 12.87%) | 4.00% (2.95% to 5.40%) | 95.62% (94.17% to 96.72%) | 96.00% (94.60% to 97.05%) |

### Other-domain compared with banking

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.6429 | 375 | 24 | 426 | 75 | 5.33% (3.61% to 7.81%) | 16.67% (13.51% to 20.39%) | 93.98% (91.21% to 95.92%) | 83.33% (79.61% to 86.49%) |
| FPR ≤ 1% | 0.8164 | 354 | 5 | 445 | 96 | 1.11% (0.48% to 2.57%) | 21.33% (17.80% to 25.35%) | 98.61% (96.78% to 99.40%) | 78.67% (74.65% to 82.20%) |
| FPR ≤ 5% | 0.6429 | 375 | 24 | 426 | 75 | 5.33% (3.61% to 7.81%) | 16.67% (13.51% to 20.39%) | 93.98% (91.21% to 95.92%) | 83.33% (79.61% to 86.49%) |
| Baseline | 0.5000 | 383 | 44 | 406 | 67 | 9.78% (7.36% to 12.87%) | 14.89% (11.90% to 18.47%) | 89.70% (86.45% to 92.23%) | 85.11% (81.53% to 88.10%) |

### Error rates by threshold

| fail_at | FPR | FNR |
| --- | --- | --- |
| 0.0500 | 38.00% | 4.14% |
| 0.1000 | 29.11% | 5.24% |
| 0.1500 | 25.56% | 5.52% |
| 0.2000 | 22.00% | 5.79% |
| 0.2500 | 20.44% | 6.21% |
| 0.3000 | 18.44% | 6.48% |
| 0.3500 | 17.11% | 6.55% |
| 0.4000 | 13.78% | 7.03% |
| 0.4500 | 11.78% | 7.24% |
| 0.5000 | 9.78% | 7.38% |
| 0.5500 | 8.44% | 7.52% |
| 0.6000 | 6.44% | 7.72% |
| 0.6500 | 5.11% | 8.34% |
| 0.7000 | 4.00% | 8.76% |
| 0.7500 | 2.22% | 9.31% |
| 0.8000 | 1.78% | 10.14% |
| 0.8500 | 1.11% | 11.38% |
| 0.9000 | 0.67% | 12.83% |
| 0.9500 | 0.22% | 18.55% |

### Reliability

The bins include their lower endpoint. The final bin also includes 1.

| Bin | N | Mean P(yes) | Observed positive rate |
| --- | --- | --- | --- |
| 0.0 to 0.1 | 395 | 2.68% | 19.24% |
| 0.1 to 0.2 | 40 | 14.52% | 20.00% |
| 0.2 to 0.3 | 26 | 25.23% | 38.46% |
| 0.3 to 0.4 | 29 | 36.64% | 27.59% |
| 0.4 to 0.5 | 23 | 45.55% | 21.74% |
| 0.5 to 0.6 | 20 | 55.44% | 25.00% |
| 0.6 to 0.7 | 26 | 64.35% | 57.69% |
| 0.7 to 0.8 | 30 | 74.61% | 66.67% |
| 0.8 to 0.9 | 44 | 85.36% | 88.64% |
| 0.9 to 1.0 | 1267 | 98.64% | 99.76% |

### Latency and token use

| Sample | Measurement | N | p50 ms | p95 ms | p99 ms |
| --- | --- | --- | --- | --- | --- |
| Sequential | client_latency_ms | 30 | 450.1283 | 569.7726 | 692.9213 |
| Sequential | evaluation_time_ms | 30 | 138.3500 | 181.2950 | 275.3990 |
| Up to 8 in flight | client_latency_ms | 1870 | 443.3183 | 552.1277 | 655.1698 |
| Up to 8 in flight | evaluation_time_ms | 1870 | 147.2000 | 220.9300 | 324.2490 |

| Response model | Requests | Requests with usage | Input tokens | Cost for reported tokens |
| --- | --- | --- | --- | --- |
| drex-max-v1.1 | 1900 | 1900 | 226967 | $0.00680901 |

See `drex-max-v1.1-test.csv` in the results directory for token usage per request.

## drex-v2.0

banking 450, oos 1000, other 450.

ROC AUC 0.9521. PR AUC 0.9862 (trapezoidal).
Brier score 0.0802.
ECE 5.38% (95% bootstrap interval 4.28% to 6.74%).

### Held-out thresholds

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.7948 | 1328 | 69 | 381 | 122 | 15.33% (12.30% to 18.95%) | 8.41% (7.09% to 9.95%) | 95.06% (93.80% to 96.08%) | 91.59% (90.05% to 92.91%) |
| FPR ≤ 1% | 0.9647 | 1226 | 11 | 439 | 224 | 2.44% (1.37% to 4.32%) | 15.45% (13.68% to 17.40%) | 99.11% (98.41% to 99.50%) | 84.55% (82.60% to 86.32%) |
| FPR ≤ 5% | 0.9240 | 1277 | 27 | 423 | 173 | 6.00% (4.16% to 8.59%) | 11.93% (10.36% to 13.70%) | 97.93% (97.00% to 98.57%) | 88.07% (86.30% to 89.64%) |
| Baseline | 0.5000 | 1368 | 112 | 338 | 82 | 24.89% (21.12% to 29.08%) | 5.66% (4.58% to 6.97%) | 92.43% (90.97% to 93.67%) | 94.34% (93.03% to 95.42%) |

### CLINC150 out-of-scope compared with banking

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.7948 | 956 | 69 | 381 | 44 | 15.33% (12.30% to 18.95%) | 4.40% (3.29% to 5.86%) | 93.27% (91.57% to 94.65%) | 95.60% (94.14% to 96.71%) |
| FPR ≤ 1% | 0.9647 | 906 | 11 | 439 | 94 | 2.44% (1.37% to 4.32%) | 9.40% (7.74% to 11.37%) | 98.80% (97.86% to 99.33%) | 90.60% (88.63% to 92.26%) |
| FPR ≤ 5% | 0.9240 | 935 | 27 | 423 | 65 | 6.00% (4.16% to 8.59%) | 6.50% (5.13% to 8.20%) | 97.19% (95.95% to 98.06%) | 93.50% (91.80% to 94.87%) |
| Baseline | 0.5000 | 974 | 112 | 338 | 26 | 24.89% (21.12% to 29.08%) | 2.60% (1.78% to 3.78%) | 89.69% (87.74% to 91.36%) | 97.40% (96.22% to 98.22%) |

### Other-domain compared with banking

| Selection | fail_at | TP | FP | TN | FN | FPR | FNR | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best F1 | 0.7948 | 372 | 69 | 381 | 78 | 15.33% (12.30% to 18.95%) | 17.33% (14.12% to 21.10%) | 84.35% (80.67% to 87.45%) | 82.67% (78.90% to 85.88%) |
| FPR ≤ 1% | 0.9647 | 320 | 11 | 439 | 130 | 2.44% (1.37% to 4.32%) | 28.89% (24.89% to 33.24%) | 96.68% (94.15% to 98.13%) | 71.11% (66.76% to 75.11%) |
| FPR ≤ 5% | 0.9240 | 342 | 27 | 423 | 108 | 6.00% (4.16% to 8.59%) | 24.00% (20.28% to 28.16%) | 92.68% (89.56% to 94.92%) | 76.00% (71.84% to 79.72%) |
| Baseline | 0.5000 | 394 | 112 | 338 | 56 | 24.89% (21.12% to 29.08%) | 12.44% (9.71% to 15.82%) | 77.87% (74.05% to 81.27%) | 87.56% (84.18% to 90.29%) |

### Error rates by threshold

| fail_at | FPR | FNR |
| --- | --- | --- |
| 0.0500 | 83.56% | 1.17% |
| 0.1000 | 60.22% | 2.62% |
| 0.1500 | 46.67% | 3.59% |
| 0.2000 | 40.44% | 4.41% |
| 0.2500 | 35.33% | 4.55% |
| 0.3000 | 31.78% | 5.03% |
| 0.3500 | 30.44% | 5.24% |
| 0.4000 | 28.44% | 5.38% |
| 0.4500 | 26.44% | 5.52% |
| 0.5000 | 24.89% | 5.66% |
| 0.5500 | 23.56% | 6.28% |
| 0.6000 | 22.44% | 6.41% |
| 0.6500 | 20.22% | 6.69% |
| 0.7000 | 18.44% | 7.24% |
| 0.7500 | 17.33% | 7.66% |
| 0.8000 | 14.89% | 8.55% |
| 0.8500 | 11.78% | 9.86% |
| 0.9000 | 8.67% | 11.03% |
| 0.9500 | 4.00% | 13.52% |

### Reliability

The bins include their lower endpoint. The final bin also includes 1.

| Bin | N | Mean P(yes) | Observed positive rate |
| --- | --- | --- | --- |
| 0.0 to 0.1 | 217 | 5.61% | 17.51% |
| 0.1 to 0.2 | 115 | 13.93% | 22.61% |
| 0.2 to 0.3 | 48 | 24.43% | 18.75% |
| 0.3 to 0.4 | 20 | 35.14% | 25.00% |
| 0.4 to 0.5 | 20 | 44.36% | 20.00% |
| 0.5 to 0.6 | 22 | 54.34% | 50.00% |
| 0.6 to 0.7 | 30 | 65.28% | 40.00% |
| 0.7 to 0.8 | 35 | 76.00% | 54.29% |
| 0.8 to 0.9 | 64 | 84.98% | 56.25% |
| 0.9 to 1.0 | 1329 | 98.74% | 97.07% |

### Latency and token use

| Sample | Measurement | N | p50 ms | p95 ms | p99 ms |
| --- | --- | --- | --- | --- | --- |
| Sequential | client_latency_ms | 30 | 350.3472 | 377.9527 | 434.3416 |
| Sequential | evaluation_time_ms | 30 | 41.7790 | 46.3490 | 46.6590 |
| Up to 8 in flight | client_latency_ms | 1870 | 359.1243 | 475.7799 | 634.7339 |
| Up to 8 in flight | evaluation_time_ms | 1870 | 41.4675 | 76.6141 | 198.9814 |

| Response model | Requests | Requests with usage | Input tokens | Cost for reported tokens |
| --- | --- | --- | --- | --- |
| drex-v2.0 | 1900 | 1900 | 114867 | $0.00344601 |

See `drex-v2.0-test.csv` in the results directory for token usage per request.
