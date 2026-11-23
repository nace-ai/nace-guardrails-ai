# Distribute DrexPolicy

Keep `pip install drex-policy` as the primary install path. The import is `from drex_policy import DrexPolicy`.

The `guardrails hub install` command is retired.

## Add the Drex integration page

After approval, copy [the page draft](drex-docs-page.mdx) to `docs-site/integrations/guardrails-ai.mdx` in the Drex repo. Add `"integrations/guardrails-ai"` to the Integrations group in `docs-site/docs.json`. The Drex docs test checks that each published page has a navigation entry.

Use released model IDs or a served alias in code examples. The draft uses the validator default. Run the Drex docs tests when adding the page. The model example check and navigation check are in `tests/unit/docs.test.ts`.

## Restore the Guardrails listing

After separate approval, update the existing branch `nace-ai/guardrails-hub-monorepo:alish/drex-policy` with the final 0.2.0 code and tests. Use `drex_policy/py/src/guardrails_ai/drex_policy/` and keep the registered name `nace/drex_policy`. Run the monorepo lint, type, test, and license checks. Regenerate `requirements.lock`.

The planned listing uses the monorepo package name `guardrails-ai-drex-policy` and import `guardrails_ai.drex_policy`. Both packages contain the same validator. Their READMEs should present `drex-policy` as the primary install path. [Monorepo submission requirements](https://github.com/guardrails-ai/guardrails-hub-monorepo#adding-a-validator)

Pull request 22 and its author's profile return 404. The repository event history showed neither a close nor a merge. The likely cause is that GitHub hid the author's account and public contributions. If pull request 22 is still hidden, use an active account to open a replacement after the user approves the text. Refer to the original pull request in the description. If it returns, continue there. [Pull request 22](https://github.com/guardrails-ai/guardrails-hub-monorepo/pull/22)

## Update PyPI trusted publishing

The 0.1.0 provenance names `nace-ai/drex_policy`, workflow `publish.yml`, and environment `pypi`. The repository now uses `nace-ai/nace-guardrails-ai`. A PyPI project owner must approve and update the trusted publisher before the next release. The evidence identifies the old publisher. Project ownership still needs confirmation. [0.1.0 provenance](https://pypi.org/integrity/drex-policy/0.1.0/drex_policy-0.1.0-py3-none-any.whl/provenance)
