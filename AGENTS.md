# Notes for coding agents

This repo is `nace-ai/nace-guardrails-ai` (Drex for Guardrails AI), published to PyPI as `drex-policy`. Its Guardrails validator, `DrexPolicy`, asks Drex one `noul` or `score` question through `nace-sdk`. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and release checks.

## Rules

- Call Drex only through `nace-sdk`.
- The default model is the pinned ID `drex-max-v1.1`. Send explicit model IDs and aliases unchanged.
- Reject images with the text-only model `drex-v2.0` and more than eight images before the call. Keep the image order for all other IDs. Accept base64, data URLs, and values from `nace_sdk.image_input()`. Raise `DrexPolicyError` for bad image input.
- Offline tests use fake clients or an SDK client with `httpx.MockTransport`. They need no key or network. Keep live tests under the `live` marker.
- Declare each third-party package imported by `drex_policy/` in the project dependencies. Declare test and lint tools in the `dev` extra.
- List keys in `.env.example` without values. Keep `.env` out of version control.
- Run `make qa` and `make artifact-test` before you push.
- Keep the package directory `drex_policy/`. A top-level `validator` package would collide with other validators.
- The code is MIT-licensed, copyright Nace.AI. Use original code in this repository.
