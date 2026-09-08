---
name: xero
description: Use when working with Xero through the bundled Desktop-managed OAuth connection, including authorization, tenant selection, and supported accounting workflows such as creating purchase bills with line items and attachments.
metadata:
  version: "0.1.0"
  hermes:
    tags: [xero, accounting, bills, attachments, singapore]
---

# Xero

Use the bundled deterministic CLI; do not hand-build Xero HTTP requests or recreate its OAuth, contact, item, attachment, or approval logic. Resolve the loaded Skill directory from this `SKILL.md`, then run its `scripts/xero.py` (for example, `<skill-dir>/scripts/xero.py`). Use the real loaded path only for tool execution. In user-facing explanations, examples, and saved reports, show `<skill-dir>` instead of an installed absolute path; never echo a machine-specific installation path. Treat its single JSON stdout document as the authority for the outcome; never infer a successful approval from a plan, an exit code alone, or an unobserved API response.

Laiye Worker supplies this skill's public OAuth configuration as the paired `CLAWWORKER_XERO_OAUTH_CLIENT_ID` and `CLAWWORKER_XERO_OAUTH_REDIRECT_URI` process variables. Do not set, print, or substitute either value in normal Desktop use. An external runtime must set both variables itself before it starts the skill. They contain no client secret. Run `doctor` to check `oauth_configured` and callback-port readiness without exposing either value.

## Prepare and run

1. Run `python <skill-dir>/scripts/xero.py doctor` and inspect its JSON `checks`. Do not use `doctor` to make an API call. If any dependency is `false`, install the pinned runtime dependencies into the same Python interpreter with `python -m pip install --requirement "<skill-dir>/requirements.lock"`, then rerun `doctor`. Do not continue to authentication or Xero operations until every dependency is `true`; if installation fails or `pip` is unavailable, report the setup failure instead of improvising another environment.
2. Check authorization with `auth status`; treat `doctor`'s `checks.authentication_exists` as a diagnostic only. The default `auto` store uses the same portable `keyring` interface on Windows and macOS: Windows Credential Manager or macOS Keychain holds one short random encryption key, while the full Xero token bundle exists only as AES-GCM ciphertext in the platform user-data directory. Do not create, read, copy, or retain `xero_token_passphrase.txt` or any other passphrase file in a workspace, repository, session directory, shell profile, command argument, JSON, log, or report.
   - On `TOKEN_STORE_MIGRATION_REQUIRED`, do not run `auth login`. Ask the operator to inject the existing passphrase as `XERO_TOKEN_PASSPHRASE` for one `auth status` process. A successful status migrates the old encrypted file to the OS-backed store and removes that old token file; unset the variable afterward. The operator may then securely remove any pre-existing plaintext passphrase file.
   - Use `--token-store encrypted-file` only when the OS keyring is unavailable. It requires the operator to supply the passphrase to every new process; never persist it for convenience.
   - On `TOKEN_STORE_KEY_MISSING`, `TOKEN_DECRYPT_FAILED`, or another token-store integrity error, report the error and require operator recovery or explicit local credential reset. If the operator chooses reset, `auth logout --local-only` removes current and legacy local credentials without first decrypting them; it does not revoke the Xero grant. If `TOKEN_STORE_UNAVAILABLE` occurs while OS-backed or legacy keyring state may exist, unlock or restore the same user's credential store and retry; do not fall back or log in again. Do not overwrite the store or infer that a fresh Xero login is safe.
   - Run `auth login` only after `auth status` reports `authenticated: false` with no migration/integrity error, or after a genuine Xero authorization failure. The CLI preflights secure storage before opening the browser. The app uses public PKCE; never ask for a Client Secret or expose access tokens, refresh tokens, passphrases, encryption keys, or authorization headers. Do not copy the OS-backed token files between computers; each machine's ciphertext is bound to its own user keyring.
3. Construct a JSON request and validate it before `create`. Required fields are `from`, `reference`, `permit_number`, non-empty `items` (`code`, positive `qty`), and non-empty `attachments` (maximum ten files, 10 MiB each). Attachment paths may be absolute or relative: write an absolute path such as `/tmp/invoice.pdf` directly into the JSON and do not move or copy it; resolve a relative path against the input JSON's directory. `date` and `due_date` are optional ISO dates. Preserve the mapping: `reference` becomes Xero `InvoiceNumber`; `permit_number` becomes Xero `Reference`. Do not ask for SKU purchase account, tax, or price fields: the CLI obtains complete purchase details from the existing Xero Item.
4. Run `python <skill-dir>/scripts/xero.py create --input <request.json>` (and `--tenant-id <UUID>` only when the user supplies it). The CLI resolves exactly one existing active Contact. Never create a Contact; report `CONTACT_NOT_FOUND` or `CONTACT_AMBIGUOUS` instead. Do not supply `--retry-unknown` unless the CLI returned that exact pending attempt ID and the identical input and tenant are being retried before its `expires_at` time.

The CLI creates a DRAFT, then uploads every attachment. It may update to `AUTHORISED` only when every requested SKU succeeds with no `skipped_items` and every attachment upload succeeds. If any attachment upload fails, preserve and report the partial DRAFT; do not separately approve it. The CLI reads back the result. Do not separately submit Xero writes or approve a bill yourself.

## Report the observed JSON result

Parse and report the CLI fields that are present: `ok`, `message`, `code`, `details`, `invoice_id`, `invoice_number`, `permit_number`, `final_status`, `tenant_id`, `tenant_name`, `skipped_items`, `attachments`, and `warnings`. Keep sensitive values out of reports. An explicit `--tenant-id` may omit `tenant_name`; never reuse a name from another stored tenant.

| Observed CLI JSON | Report and next action |
| --- | --- |
| `ok: true`, `final_status: AUTHORISED` | Report the bill as approved, including `invoice_id`, attachments, and any warnings. This is the only normal successful approval. |
| `ok: true`, `final_status: DRAFT` | Report that the bill was created but remains DRAFT for the reason in `warnings` (for example SKU review or the Demo approval gate); include `skipped_items` when present. Do not call it approved. |
| `code: CREATE_OUTCOME_UNKNOWN` or `CREATE_RECONCILIATION_REQUIRED` | State that Xero may or may not have created the draft. Do not call this a no-create result. Preserve the attempt ID and follow the JSON `operator_actions`; never start a fresh create. |
| `ok: false` without `invoice_id`, excluding the unknown-create codes above | Report a no-create failure using `code`, `message`, and `details`; no bill is known to have been created. |
| `ok: false` with `invoice_id` | Report a partial operation, its `invoice_id`, `final_status`, attachment results, and warnings. If `final_status` is `UNKNOWN`, require manual inspection. |
| `ok: false`, `final_status: AUTHORISED` | State only that a read-back observed `AUTHORISED` after a failed operation; it is not a successful approval claim. Include warnings and require the user to verify before further action. |

## Reconcile an unknown create

`create-state status` is local-only and shows the single bounded pending record. Xero caches an idempotency key for six minutes. Before `expires_at`, the operator may explicitly retry the identical request and tenant with `create --input <same-request.json> --retry-unknown <attempt-id>`; the CLI reuses the persisted key. At or after expiry, do not retry. Inspect Xero for the requested bill first, then clear only with `create-state clear --attempt-id <attempt-id> --confirmed-inspected`. Clearing is an operator assertion that inspection is complete, not evidence that Xero did or did not create the bill.

## Guarded Demo Company smoke

Use this procedure only when the user explicitly authorizes a live Xero Demo Company write. For a draft-only smoke, run `demo-smoke --input <request.json> --confirm-demo-company`; it creates, uploads, reads back, and stops at verified `DRAFT`. For an end-to-end approval smoke, obtain a separate, current approval before starting and add `--approve-live` to that initial command; with both flags, the CLI observes the newly created DRAFT before it sends approval. Do not rerun a completed draft-only smoke to approve it, because that would request another bill; this Skill has no approve-existing command. Never use `demo-smoke` against a production organisation, infer Demo status from a tenant name, or add either live flag to automated tests.
