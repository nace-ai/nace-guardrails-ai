# Drex for Guardrails AI

`DrexPolicy` is a [Guardrails AI](https://github.com/guardrails-ai/guardrails) validator for text and images. It asks [Drex](https://console.nace.ai/docs) one question and compares the answer with a threshold you set.

## Install

```bash
pip install drex-policy
```

Set `NACE_API_KEY` to a key from [the Nace console](https://console.nace.ai). You can set `NACE_BASE_URL` for a different deployment. The package supports Python 3.10 and later. See [tested versions](docs/compatibility.md).

```python
from drex_policy import DrexPolicy
```

## Check text

Use a `Noul` question for a yes/no policy. Give it instructions, describe at least one outcome in `criteria`, or do both. Set `fail_at` between 0 and 1. With `fail_when="yes"`, the guard fails when P(yes) is at or above the threshold. With `fail_when="no"`, it fails below the threshold.

```python
from guardrails import Guard
from nace_sdk import Noul

from drex_policy import DrexPolicy

guard = Guard().use(
    DrexPolicy(
        question=Noul(instructions="Does the text contain a personal phone number or email address?"),
        fail_at=0.5,
        on_fail="exception",
    )
)

guard.validate("Our office opens at 9am.")
guard.validate("Call me on +1 415 555 0100.")  # Raises if the check fails.
```

## Models and images

The default is the pinned model `drex-max-v1.1`. Set `model="drex-v2.0"` to keep the 0.1.0 default. Explicit IDs and aliases go to Drex unchanged. Drex currently resolves `drex-latest` and the retired `drex-max` to `drex-max-v1.1`. See the [model catalog](https://console.nace.ai/docs/drex/reference/models).

Pass images in `metadata["images"]`. Use `nace_sdk.image_input()` with local paths, bytes, or workspace file references. Base64 strings and data URLs also work. Download web images first and pass the bytes to `image_input()`. The validator refuses HTTP and HTTPS image strings and images with the text-only model `drex-v2.0` before sending a request. Drex checks image support for other IDs. See the [Drex docs](https://console.nace.ai/docs) for current limits.

Use a `Score` question for an ordered rubric. Set `fail_at` to a finite expected-index threshold. The default `fail_when="at_or_above"` fails at the threshold or higher. Use `fail_when="below"` to fail below it.

```python
from base64 import b64decode

from guardrails import Guard
from nace_sdk import Score, image_input

from drex_policy import DrexPolicy

# A one-pixel PNG. Use image_input("listing.png") for a local image.
png = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGP4DwABAQEAsTj2FAAAAABJRU5ErkJggg=="
)
guard = Guard().use(
    DrexPolicy(
        question=Score(
            instructions="How unsafe is this listing for a general audience?",
            criteria=["safe", "borderline", "unsafe"],
        ),
        fail_at=1.5,
        on_fail="exception",
    )
)
guard.validate("Vintage camera, works well.", metadata={"images": [image_input(png)]})
```

## Input and output checks

Use `Guard().use(policy, on="messages")` to check message content before calling an LLM. Use `Guard().use(policy)` for a string result, or attach the validator to a Pydantic field as shown below.

Pass validators together as `Guard().use(a, b)` to check the same output with both. Each policy makes its own Drex request. Calling `Guard().use(a).use(b)` replaces the output validators with `b`. Set a distinct `question_id` to identify failures and their metadata.

```python
from guardrails import Guard
from nace_sdk import Noul
from pydantic import BaseModel, Field

from drex_policy import DrexPolicy

off_topic = DrexPolicy(
    question=Noul(instructions="Is this reply about something other than our billing product?"),
    fail_at=0.5,
    on_fail="exception",
)


class Reply(BaseModel):
    answer: str = Field(validators=[off_topic])


guard = Guard.for_pydantic(output_class=Reply)
guard.parse('{"answer": "Your invoice is in Settings > Billing."}')
```

## Policy failures

| `on_fail` | Guardrails action |
| --- | --- |
| `exception` | Raises `ValidationError` with the policy failure message. |
| `noop` | Keeps the original value and reports a validation failure. |
| `filter` | Removes the failing value. A string guard returns `None`. |
| `refrain` | Suppresses the output. A string guard returns `None`. |
| `reask` | Produces a reask for the value. A guard with an LLM can request another response. |
| `fix` | Replaces the value with `fix_value`. |
| `fix_reask` | Checks `fix_value` in a second Drex request. Only a real passing Drex decision accepts the replacement. A fail or no decision raises `DrexPolicyError`, for every `on_error` setting. Caller errors and cancellation keep their own exceptions. |
| Callable | Calls `handler(value, fail_result)` once and uses its return value. |

Set `fix_value` to a string when using `fix` or `fix_reask`. The constructor raises `ValueError` when the replacement is missing. With `fix_reask`, text equal to `fix_value` must receive a real passing Drex decision. Availability failures raise `DrexPolicyError` even with `on_error="pass"`. Caller errors such as HTTP 401, 402 and 422, and cancellation, keep their own exceptions.

## Drex errors

`on_error="raise"` is the default. SDK availability errors propagate. An absent answer, the wrong answer type, a non-finite value, or a noul outside 0 to 1 raises `DrexPolicyError`.

Set `on_error="fail"` to return a failed result when Drex is unavailable or its answer is unusable. Guardrails then applies `on_fail`. For example, `on_fail="exception"` raises `ValidationError`. A reask generates new text and cannot restore an unavailable service.

**Pass-through can allow unchecked content during an outage.** Use it only for advisory checks.

Set `on_error="pass"` to allow pass-through for availability errors. These include every connection failure, timeouts, and HTTP 408, 429 and 5xx after SDK retries. A response-validation error counts as availability only with one of those HTTP statuses. With any other status, including HTTP 200 or an absent status, it is an unusable answer. Connection failures caused by a wrong `NACE_BASE_URL` count too. Watch the `drex_policy` warning log when you enable pass-through. An unusable answer still raises `DrexPolicyError` with this setting.

Caller errors always raise, including invalid credentials, insufficient credit, invalid requests, invalid images and non-string input. Error result metadata includes the error type, HTTP status, and request ID. Its message uses the type and status only, so server error text stays out of Guard history.

## Async and streaming

`AsyncGuard` can use a native `AsyncNaceClient`. Pass `async_client=` to supply your own. If you supply only a sync `client`, Guardrails runs the check in a worker thread. Cancelling native async validation stops the local request and propagates `CancelledError`. The thread path checks a cancellation flag immediately before starting a request. A request already in flight on the thread path cannot be stopped. Cancellation and stop signals always propagate.

```python
import asyncio

from guardrails import AsyncGuard
from nace_sdk import Noul

from drex_policy import DrexPolicy


async def check():
    policy = DrexPolicy(Noul(instructions="Does the reply ask for a password?"), 0.5)
    try:
        return await AsyncGuard().use(policy).validate("Open Settings to change your password.")
    finally:
        await policy.aclose()


asyncio.run(check())
```

The validator keeps one owned async client for each event loop and locks client creation across threads. Use `await aclose()` before shutting down the loops that performed validation. It closes all owned clients, including clients on other running loops. `close()` closes the owned sync client and releases the async clients it can close. With a running loop, `close()` schedules async cleanup. Await `aclose()` to finish that cleanup. Connections from an already closed loop may be impossible to close fully, and cleanup logs a warning if that happens. Guardrails may also run a synchronous `Guard` through its async validator service. If you pass a client, you manage its lifetime. To add attribution, build it with `NaceClient(client_info="drex-policy/<version>")` or use the same option with `AsyncNaceClient`. The validator uses the installed package version when it builds a client.

Guardrails checks streamed text as it completes sentences. Each sentence check sends one Drex request. Drex sees only the current chunk.

**A sync stream can emit its last fragment without checking it.** In Guardrails 0.11.0, with `on_fail="exception"` or `"noop"`, a final fragment without sentence-ending punctuation makes no Drex request. Guardrails keeps that fragment on the validator, so a later stream can complete the earlier sentence. Validate the complete text once more after the stream. Use one validator object for each stream.

Async streaming uses the inherited Guardrails executor path and the sync client. Pass `client` or set `NACE_API_KEY`, even if you supplied `async_client`. Cancellation does not stop a streamed request already running in the worker thread.

## Result metadata

After validation, read `guard.history.last.validator_logs[i].validation_result.metadata`, where `i` is the log index. Every result copies the input metadata and adds the `drex_*` fields on top. Images and keys used by other validators survive sequential checks and replacement rechecks. `DrexPolicy` leaves its input dictionary intact. Guardrails may update the dictionary passed to `Guard.validate()`. Pass a copy there if you need to preserve it.

| Key | Value |
| --- | --- |
| `drex_model`, `drex_response_model` | Requested ID and the model that answered. |
| `drex_question_id`, `drex_answer` | Question ID and the SDK answer with absent fields omitted. |
| `drex_usage`, `drex_request_id`, `drex_evaluation_time_ms` | Token usage, a validated request ID, and model time when available. |
| `drex_measured`, `drex_fail_at`, `drex_fail_when` | Measurement, threshold, and comparison direction. |
| `drex_error` | Type, HTTP status, request ID, and a message built from the type and status when `on_error` returns a result. This result has no `drex_answer`. |

Both request-ID fields accept only 1 to 128 characters from `[A-Za-z0-9._:-]`. Other values become `None`, including server body text that the SDK returns as a fallback ID.

## Upgrade from 0.1.0

- The default model changes to `drex-max-v1.1`. Set `model="drex-v2.0"` to keep the previous model, and recheck your threshold when changing models.
- Images are accepted with model IDs other than the known text-only `drex-v2.0`. Other IDs are sent unchanged for Drex to check.
- An absent or unusable answer raises `DrexPolicyError`, including with `on_error="pass"`. Choose `on_error="fail"` to fail closed.
- `on_fail="fix"` and `"fix_reask"` require `fix_value` at construction. Version 0.1.0 accepted a missing replacement, then returned `None` or raised `TypeError` later.
- A `Noul` needs instructions or a described outcome. An empty question raises `ValueError` at construction. Version 0.1.0 accepted it and the SDK refused it at the first call.

## Choose `fail_at`

Use labeled examples from your application to choose a threshold. Recheck it when you change models or questions. The [evaluation guide](eval/README.md) explains how to measure false-positive and false-negative rates on a banking support policy.

<!-- eval-table:start -->
CLINC150, collection started 2026-11-23. These results apply to this dataset and this question.

| Model | Selection | fail_at | Test FPR (95% CI) | Test FNR (95% CI) | ECE | ECE 95% interval |
| --- | --- | --- | --- | --- | --- | --- |
| drex-max-v1.1 | Best F1 | 0.6429 | 5.33% (3.61% to 7.81%) | 8.28% (6.97% to 9.81%) | 5.52% | 4.66% to 6.78% |
| drex-max-v1.1 | FPR ≤ 1% | 0.8164 | 1.11% (0.48% to 2.57%) | 10.55% (9.07% to 12.24%) | 5.52% | 4.66% to 6.78% |
| drex-max-v1.1 | FPR ≤ 5% | 0.6429 | 5.33% (3.61% to 7.81%) | 8.28% (6.97% to 9.81%) | 5.52% | 4.66% to 6.78% |
| drex-max-v1.1 | Baseline | 0.5000 | 9.78% (7.36% to 12.87%) | 7.38% (6.14% to 8.84%) | 5.52% | 4.66% to 6.78% |
| drex-v2.0 | Best F1 | 0.7948 | 15.33% (12.30% to 18.95%) | 8.41% (7.09% to 9.95%) | 5.38% | 4.28% to 6.74% |
| drex-v2.0 | FPR ≤ 1% | 0.9647 | 2.44% (1.37% to 4.32%) | 15.45% (13.68% to 17.40%) | 5.38% | 4.28% to 6.74% |
| drex-v2.0 | FPR ≤ 5% | 0.9240 | 6.00% (4.16% to 8.59%) | 11.93% (10.36% to 13.70%) | 5.38% | 4.28% to 6.74% |
| drex-v2.0 | Baseline | 0.5000 | 24.89% (21.12% to 29.08%) | 5.66% (4.58% to 6.97%) | 5.38% | 4.28% to 6.74% |
<!-- eval-table:end -->

See the [runnable examples](examples/) for input, output, async, and image checks. Run each example with `--fake` for an offline demo.

## License

MIT. Copyright (c) 2026 Nace.AI. See [LICENSE](LICENSE).
