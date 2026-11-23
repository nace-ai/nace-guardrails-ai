# DrexPolicy third formal check

**Status — pass.** All ten properties passed, all 23 variants were killed, and each variant check passed on its unmutated control.

The checked source is the 0.2.0 change on top of `babb3e5`. The `drex_policy/main.py` SHA-256 is `9006a0de5eabb9b3a6229abbe9448d44355be78b7d2aec159e49ee6552934c28`. The replay uses Python 3.12.11, Guardrails AI 0.11.0, nace-sdk 0.11.2, and Hypothesis 6.170.17.

Activate the worktree's `.venv` and run `make formal` from the repository root. The tools jar must already be present at `formal/.tools/tla2tools-1.7.4.jar`. The runner validates its pinned SHA-256, blocks Python DNS and socket connections, and uses fake clients or `httpx.MockTransport` without an API key. `OfflineTLC.java` supplies an unbound replacement for the standalone fingerprint store's unused RMI listener. TLC's state storage and checking algorithms use the original TLA+ Tools 1.7.4 jar, TLC 2.19. See [SKILL.md](SKILL.md) for setup and maintenance.

## Properties and mutation checks

| Property | Checked rule | Tool | Variants |
| --- | --- | --- | --- |
| P1 | One correction per failure or an exception propagates. A replacement needs a real passing Drex decision. | TLC and Guard/AsyncGuard replay, Hypothesis | Duplicate failure delivery, pass after failure, disabled replacement guard, and applying `on_error` to an undecided replacement |
| P2 | A pass requires a usable passing answer or an availability error with `on_error="pass"`. Unusable data never passes. | TLC, Hypothesis, and Guard/AsyncGuard replay | Removed finite check, 401 as availability, and invalid HTTP 200 data as availability |
| P3 | Cancellation stays a stop signal. Pre-commit thread cancellation prevents the request. Native cancellation stops transport. | TLC and Guard/AsyncGuard replay | Cancellation consumed by the error handler and removed cancellation-flag check |
| P4 | Finite threshold comparisons have the configured direction, equality behavior, and monotonicity. | Hypothesis | `>=` replaced by `>` and exchanged branches |
| P5 | The SDK answer fields and resolved response model are preserved. | Hypothesis and Guard/AsyncGuard replay | Rounded probability, omitted confidence, and request alias substituted for response model |
| P6 | One SDK call per validation, zero for preflight refusal. | TLC and Guard/AsyncGuard replay | A second `system_one` call after failure |
| P7 | Accepted images retain their order, and invalid image input is refused before the SDK call. | Hypothesis and SDK mock transport | Dropped text-only-model images, skipped mapping validation, and skipped data-URL base64 validation |
| P8 | Two overlapping calls on one validator use their own answers. | TLC and Guard/AsyncGuard replay | Shared last-answer field |
| P9 | Every result retains ordinary caller metadata and chain/replacement checks retain images. | Hypothesis and Guard/AsyncGuard replay | Caller keys removed from both metadata builders |
| P10 | Error text is excluded and request IDs obey the strict ASCII identifier contract. | Hypothesis and SDK mock transport | `str(error)` copied into the message and request-ID validation removed |

The P1 checks exercise both original text equal to `fix_value` and the actual `fix_reask` second validation. They cover passing and failing decisions, unusable data, availability failures, all three `on_error` modes, and caller errors. A caller error must retain its original exception. The error-path guard at `main.py:395–396` runs before `on_error`, and the result guard at `main.py:435–443` accepts only a passing result for replacement text.

The P7 checks include `data:image/png;base64,!!!`, empty payloads, incomplete workspace references, non-serializable workspace IDs, and accepted payloads. The independent payload oracle checks the same declared alphabet, maximum padding, and multiple-of-four length rule as bare base64. It exercises preflight refusal through real Guardrails entry points and checks that refusal makes zero `system_one` calls.

## LIMITS

On the thread path, the cancellation-flag read immediately before `system_one` is the request commit point. Cancellation delivered before it prevents the request. The model represents the flag read and call as one atomic action under this contract. After the flag read commits the request, cancellation cannot stop it, including the interval between that read and SDK entry. Moving the final check later would leave the same scheduling window after that check. This is a limit of the code. A pass establishes the pre-commit rule and never establishes the stronger guarantee that every SDK entry after cancellation is prevented. An already committed sync request may finish while the caller receives `CancelledError`. Native async cancellation has the stronger requirement that it stops the transport call.

The full validation model covers one original validation and at most one replacement, three error modes, four framework routes, eight correction actions on ordinary failure, and `noop`/`fix_reask` over the larger reply matrix. The concurrency model covers two calls sharing one validator with distinct Noul values, 0.1 and 0.9. Every non-stuttering terminal model path is replayed against the actual validator and Guardrails. Native result handling has no await between response completion and result construction. Worker response handling can switch between answer extraction and result construction.

Generated checks use up to 80 examples per function plus explicit boundaries. They cover finite binary64 comparisons, Noul values in `[0,1]`, unusable values, up to five Score probabilities, zero to ten images, data-URL payloads through length 40, ordinary metadata keys and nested values, SDK error classes and statuses, and request-ID boundaries. P10 permits structured status and request IDs matching `[A-Za-z0-9._:-]{1,128}`, including the SDK's body fallback. It excludes server message text and identifiers outside that pattern.

Live transport, retry timing, client lifecycle, other dependency versions, streaming, more than one replacement, more than two concurrent calls, and arbitrary inputs beyond the generated samples are outside the checked scope. P6 counts validator calls to `system_one`, which can perform SDK retries internally. The formal runner stays outside default pytest collection. All files under `formal/` are available to git except `.tools/` and caches, and the runner checks that condition.

The retained [evidence record](evidence.json) contains the final property verdicts, all mutation results, model counts, replay counts, source hash, and dependency versions. Detailed TLC logs and replay logs are stored in the evidence directory printed by `make formal`.

## Model and replay results

The full TLC check explored 2,321 validation states and 41 concurrency states without invariant violations. The replay checked all 933 validation terminal paths and all 46 concurrency terminal paths. The full validation graph exceeds `/statechart`'s chart budget, so the [validation diagram](validation.mmd) uses `ValidationChart.cfg`, a separately checked slice with `on_error="pass"`, `on_fail="fix_reask"`, and native/executor routes. The slice has 197 states and the diagram has 13 visible states and 20 arrows. It omits 96 steps that keep the same visible label. The [concurrency diagram](concurrency.mmd) shows the complete bounded concurrency model, merging equal visible phases and omitting terminal self-loops. The full property verdict comes from the unrestricted model configuration.

The final `make qa` run passed lint, type checking, and 412 tests, with 14 live cases deselected.
