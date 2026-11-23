# Contributing to Drex for Guardrails AI

The `nace-ai/nace-guardrails-ai` repository contains the `drex-policy` package. Import `DrexPolicy` and `DrexPolicyError` from `drex_policy`.

## Set up

1. Create and activate a virtual environment with Python 3.10 or later.
2. Run `make dev` to install the package and development tools.
3. Run `make qa` for Ruff, Pyright, and the offline tests.

Keep the package in `drex_policy/`. A top-level `validator` module would collide with other validators. Use the SDK for all Drex requests. The default model is `drex-max-v1.1`. Send explicit IDs unchanged. Reject images for `drex-v2.0`. See [image handling](README.md#models-and-images).

## Verify a release candidate

Install [uv](https://docs.astral.sh/uv/) and make it available on your path.

- Run `make artifact-test` to build the wheel and sdist. The check installs each archive in its own environment and runs the offline tests and examples.
- Run `make matrix` with Python 3.10 through 3.14 installed. The script writes the results to [docs/compatibility.md](docs/compatibility.md). The local logs are in `.compat-logs/`.
- Run `make check-install-guidance` to check the installation instructions.
- To opt in to live checks, set `NACE_API_KEY` in your environment and run `make live`. Keep the key out of files and logs. The manual GitHub workflow requires a configured secret.
- See the [evaluation guide](eval/README.md) for threshold measurements.

Run `make formal` separately to check the formal properties. It requires Java 11 or later and the TLA+ tools jar. See [formal/SKILL.md](formal/SKILL.md) for the jar path and setup instructions.

## Release

Set the version in [pyproject.toml](pyproject.toml) and record changes in [CHANGELOG.md](CHANGELOG.md). Confirm the PyPI trusted publisher with the project owner before an approved release.

The [publish workflow](.github/workflows/publish.yml) runs when a matching version tag is pushed. The tag must match the project version.
