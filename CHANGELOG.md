# Changelog

## 0.2.0

### Behavior changes

- The default model is now the pinned `drex-max-v1.1`. Set `model="drex-v2.0"` to keep the previous model. Check `fail_at` against labeled examples when switching models.
- Images are accepted for model IDs other than the known text-only `drex-v2.0`. The validator forwards these IDs unchanged for Drex to check. Use `image_input()` for local bytes, paths, or workspace file references.
- An absent answer, wrong answer type, non-finite measurement, or noul outside 0 to 1 raises `DrexPolicyError`. This also applies with `on_error="pass"`. Use `on_error="fail"` for a failed result.
- `on_fail="fix"` and `"fix_reask"` without `fix_value` raise `ValueError` at construction. Version 0.1.0 accepted this configuration, then returned `None` or raised `TypeError` later.
- A `Noul` without instructions and without a described outcome raises `ValueError` at construction. Version 0.1.0 accepted it and the SDK refused it at the first call.

### Improvements

- `on_error` adds explicit availability handling. The default raises. Choose `"fail"` to apply `on_fail`, or `"pass"` for advisory checks that can allow unchecked content during an outage. Every connection failure counts, including a wrong base URL. Monitor the `drex_policy` warning log when using `"pass"`.
- Response-validation errors count as availability only with HTTP 408, 429 or 5xx. Other statuses, including HTTP 200 and an absent status, follow the unusable-answer policy.
- Error result messages contain only the error type and HTTP status. Both request-ID fields accept only 1 to 128 ASCII letters, digits, dots, underscores, colons or hyphens. Other IDs become `None`.
- `fix_reask` accepts text equal to `fix_value` only after a real passing Drex decision. A fail or no decision raises `DrexPolicyError` for every `on_error` setting, including `"pass"`. Caller errors and cancellation keep their own exceptions. A passing replacement becomes the output after two checks.
- Cancellation of a thread-backed validation sets a flag checked immediately before the SDK call. A request already in flight cannot be stopped.
- Every result preserves input metadata for later validators and replacement rechecks. The validator copies the dictionary before adding its fields.
- Native async validation uses `AsyncNaceClient` and propagates cancellation. The optional `async_client` accepts a caller-owned client. Owned clients are scoped to event loops, and creation is protected across threads. `close()` schedules cleanup when called in a running loop. Await `aclose()` before shutting down the loops used for validation.
- A supplied sync client still uses the thread path when it is the only client supplied. Streaming also uses the sync client through Guardrails' executor. See the README warning about unchecked final fragments in sync streams and use a validator object for each stream.
- Guardrails can restore validators from serialized question dictionaries, including `on_error` and `fix_value`. Non-default question IDs appear in failure messages.
- Metadata includes the response model, usage, request ID, evaluation time, measurement, and threshold. The Drex answer retains its values and omits absent fields, including an absent confidence value. Attribution uses the installed package version.
- Bad image input, including HTTP URLs, incomplete workspace references, empty mappings, and data URLs without base64 content, raises `DrexPolicyError` before a request. Workspace references use strict SDK type validation. Bare base64 strings and data URL payloads share the same character, padding, and length checks.
- Guardrails AI 0.9.0 is the minimum version. Nace SDK 0.6.2 is supported. Its `User-Agent` omits the integration name. SDK 0.7.0 adds that name. See the [compatibility results](docs/compatibility.md).
- The evaluation reports PR AUC as 1.0 for samples with only positive rows. ROC AUC is unavailable for those samples. Report tables display thresholds to four decimal places while computations use the exact saved values.

### Existing tests updated for 0.2.0

- `test_pass` expects `drex-max-v1.1` in the request because the default changed.
- `test_images_without_drex_max_are_refused` checks refusal for `drex-v2.0`. The accepted cases moved to a test that checks the images sent with the default and `drex-latest` as well as other model IDs.
- `test_fail_keeps_answer_in_metadata` still checks the entire result. It expects the new default and metadata fields and keeps the assertion that absent confidence is omitted.
- `test_omitted_instructions_stay_none` uses a described outcome. It still verifies that omitted instructions stay absent. A separate test checks that an empty question fails at construction.
- `test_built_client_settings` compares attribution with the installed package version. It still checks the SDK timeout and retry settings.
- `test_readme_block_runs` now uses the real SDK and a mock transport. This catches input the SDK rejects before sending a request.

- `test_sdk_errors` now expects an unusable-answer error for an unreadable HTTP 200 body, including with `on_error="pass"`. This follows the revised response-validation status rule.
