# Xero bill CLI input contract

Run the bundled entry point from the repository root:

```text
python scripts/xero.py doctor
python scripts/xero.py auth login [--tenant-id UUID] [--token-store auto|keyring|encrypted-file]
python scripts/xero.py auth status [--token-store auto|keyring|encrypted-file]
python scripts/xero.py auth logout [--local-only] [--token-store auto|keyring|encrypted-file]
python scripts/xero.py create --input PATH [--tenant-id UUID] [--token-store auto|keyring|encrypted-file] [--retry-unknown ATTEMPT_ID]
python scripts/xero.py create-state status
python scripts/xero.py create-state clear --attempt-id ATTEMPT_ID --confirmed-inspected
python scripts/xero.py demo-smoke --input PATH --confirm-demo-company [--approve-live] [--tenant-id UUID] [--token-store auto|keyring|encrypted-file] [--retry-unknown ATTEMPT_ID]
```

Every command outcome emits exactly one UTF-8 JSON object to stdout. Browser and tenant-selection progress goes to stderr. `--debug` may be placed anywhere in the invocation; it adds a redacted traceback only for unexpected internal errors. Never parse stderr or rely on human-readable message text.

## OAuth runtime configuration

Laiye Worker provides the paired public values `CLAWWORKER_XERO_OAUTH_CLIENT_ID` and `CLAWWORKER_XERO_OAUTH_REDIRECT_URI` to Desktop-managed skill processes. The redirect URI is an HTTP IPv4-loopback or `localhost` callback URI with an explicit port. The CLI validates both values before it opens a browser or contacts Xero, and uses the same pair for authorization, exchange, refresh, and revocation. External runtimes are not Desktop-managed and must set both values themselves; neither variable has a fallback. The public-client PKCE flow never uses a client secret.

The client ID must contain a non-whitespace character and consist only of printable ASCII bytes `0x20–0x7E`; accepted spaces are preserved, while all-space, control-character, and non-ASCII IDs are rejected. Redirect hosts are limited to `localhost` or numeric IPv4 `127/8`; IPv6 is rejected. Ports must be explicit and between 1 and 65535. The callback path must be `/` or one leading slash followed by non-empty segments of ASCII RFC 3986 unreserved characters `[A-Za-z0-9._~-]`, separated by single slashes. Dot segments (`.` or `..`), missing paths, non-root trailing slashes, double slashes, percent escapes, Unicode, credentials, query/fragment delimiters, backslashes, and whitespace are rejected before the listener opens. The exact accepted URI is retained, including explicit port 80, and a multi-segment path such as `/desktop/callback` must match the callback request exactly.

## Commands

`doctor` makes no Xero request, opens no browser, and creates no Xero resource. It always exits zero and reports `oauth_configured` plus whether the configured callback port can be bound, alongside local runtime and token readiness. It validates an OS-keyring-backed encrypted token without writing a keyring probe. It validates a passphrase-encrypted file only when `XERO_TOKEN_PASSPHRASE` is already present in the process environment; otherwise `authentication_exists` is false rather than trusting file presence. It never prompts and does not print either OAuth environment value, tokens, or passphrases.

`auth login` starts the local OAuth browser flow only after the selected store has verified its key/passphrase and exercised target-directory creation, file access, flush, and atomic replacement with a non-secret probe. It selects the sole connected tenant automatically; when multiple tenants are connected it writes the numbered selection prompt to stderr. When stdin is not interactive or reaches EOF, multiple connections return `TENANT_SELECTION_REQUIRED`; use `--tenant-id`. `--tenant-id` must be a UUID and chooses a connected tenant non-interactively. `auth status` reads local credential state only and makes no Xero API call. `auth logout` first asks Xero to revoke the refresh token, then deletes local credentials only after that succeeds. `--local-only` skips token loading and revocation so it can remove corrupt, undecryptable, or migration-pending current and legacy credentials as an explicit recovery action.

`create` validates the input before contacting Xero, creates an `ACCPAY` bill as `DRAFT`, uploads attachments in input order, and only approves it after all items and attachments succeed. `--tenant-id` must be a UUID and overrides the stored tenant for this invocation; its old display name is not reused. `--token-store` defaults to `auto`. On Windows and macOS, `auto` uses the platform `keyring` backend (Windows Credential Manager or macOS Keychain) for a short random encryption key and stores the full token bundle only in an AES-GCM encrypted file under the platform user-data directory. `keyring` requires that OS-backed design. `encrypted-file` is the fallback when no usable keyring exists; it always uses the passphrase-encrypted local file without probing keyring.

An existing passphrase-encrypted `tokens.enc` is migrated automatically when `XERO_TOKEN_PASSPHRASE` is present in the process environment. The migration verifies the new ciphertext before removing the old token file, and later invocations no longer require that environment variable. Cleanup is retried on later loads if a deletion was interrupted. Without the environment value, `auto` returns `TOKEN_STORE_MIGRATION_REQUIRED` without prompting or hiding the existing authorization state. Never store the passphrase in a workspace, repository, session directory, shell profile, command argument, JSON, log, or report. For explicit `encrypted-file` use, the environment value is required in each headless process; otherwise the CLI prompts once and caches the passphrase only in that process. The CLI verifies token-store writability before opening an OAuth browser or rotating a refresh token, and blocks direct login while legacy state awaits migration. If OS-backed ciphertext or legacy keyring state exists and its keyring is locked or unavailable, `auto` fails closed with `TOKEN_STORE_UNAVAILABLE` instead of selecting an empty fallback store. OS-keyring-backed ciphertext is device/user-bound and must not be copied between machines.

If a create response is lost or invalid, the CLI returns `CREATE_OUTCOME_UNKNOWN`, `final_status: UNKNOWN`, and non-secret reconciliation details. Xero may or may not have committed the draft. A single cross-process-locked record prevents any uninformed new create. `create-state status` inspects it without network access. Before the reported six-minute `expires_at`, only the identical input and tenant may be retried with its exact `--retry-unknown` attempt ID; this reuses the same idempotency key. At or after expiry, never resend it. Inspect Xero first, then use the exact attempt ID plus `--confirmed-inspected` to clear state. Clearing records the operator decision; it does not prove the original outcome.

`demo-smoke` is a separate live Xero Demo Company procedure. The mandatory `--confirm-demo-company` flag is the first opt-in and stops at a read-back-verified DRAFT by default. For an end-to-end approval smoke, obtain a second explicit user decision before the initial invocation and add `--approve-live`; that path verifies the newly created DRAFT before sending approval. Do not rerun a completed draft-only smoke to approve it: this command would request another bill and has no approve-existing mode. Never use it against production. Automated tests use injected local fakes, never enable a live request, and make no Xero call.

## JSON input fields

`--input` is required. The file must be UTF-8 JSON whose top level is an object.

| Field | Required | Type and limit | Default / mapping |
| --- | --- | --- | --- |
| `from` | yes | non-empty string | Exact active Xero Contact name; surrounding whitespace is trimmed. |
| `reference` | yes | non-empty string | Sent as Xero `InvoiceNumber` (the bill UI Reference). |
| `permit_number` | yes | non-empty string | Sent as Xero `Reference` (Singapore Permit Number). |
| `date` | no | ISO `YYYY-MM-DD` | Local calendar date when omitted. |
| `due_date` | no | ISO `YYYY-MM-DD` | `date` when omitted. |
| `items` | yes | non-empty array | Processed in array order. |
| `items[].code` | yes | non-empty string | Existing purchasable Xero SKU, exact code. |
| `items[].qty` | yes | finite number greater than zero | No upper numeric limit is imposed locally. |
| `attachments` | yes | non-empty array, maximum 10 entries | Processed in array order. |
| `attachments[]` | yes | non-empty path string; each file at most 10 MiB | MIME type is inferred from the resolved basename, otherwise `application/octet-stream`. The basename cannot contain `< > : " / \\ | ? *`, NUL, or `+`. |

An attachment path is resolved against the directory containing the input JSON file, then normalized to an absolute path. It must exist and be a regular file. Directory separators and a native absolute-path drive/root are allowed because filename restrictions apply only to the resolved basename. Resolved basenames must be unique case-insensitively; Xero replacement semantics make duplicates unsafe. The sample's `non-production-placeholder-invoice.pdf` is deliberately relative and is not a real attachment; supply a real file beside a copied input file before running `create`.

## Result and exit contract

All result objects have `ok`. A successful create additionally uses `message`, `invoice_id`, observed `invoice_number`, observed `permit_number`, `final_status`, `tenant_id`, and, when known, `tenant_name`; it may also include `skipped_items`, complete non-secret attachment metadata, and `warnings`. Error objects use `ok: false`, `code`, `message`, and optionally sanitized `details`, `invoice_id`, observed fields, tenant context, and `final_status`. No result includes OAuth tokens, passphrases, or attachment bytes.

| Exit | Meaning |
| --- | --- |
| `0` | Command succeeded, including a known `DRAFT` result with warnings (for example a skipped SKU). |
| `1` | Unexpected `INTERNAL_ERROR`; a traceback is omitted unless `--debug` was requested, and remains redacted. |
| `2` | Invalid CLI or input document. |
| `3` | Authentication, OAuth, tenant, or token-store failure. |
| `4` | Xero API failure before a draft was known to exist, or a create whose outcome is explicitly unknown. |
| `5` | Partial operation: a draft invoice ID is known, but an attachment, approval, or later operation failed. |

## Stable reason codes

These codes are intended for callers. Messages are explanatory only and may change.

| Category | Codes |
| --- | --- |
| CLI / internal | `INVALID_ARGUMENT`, `INTERNAL_ERROR` |
| Input preflight | `INVALID_REQUEST`, `MALFORMED_JSON`, `MISSING_FIELD`, `INVALID_FIELD`, `INVALID_DATE`, `INVALID_QUANTITY`, `NO_ITEMS`, `NO_ATTACHMENTS`, `TOO_MANY_ATTACHMENTS`, `INVALID_FILENAME`, `DUPLICATE_ATTACHMENT_FILENAME`, `ATTACHMENT_NOT_FOUND`, `ATTACHMENT_NOT_FILE`, `ATTACHMENT_TOO_LARGE`, `NO_VALID_ITEMS` |
| Token storage | `INVALID_TOKEN_STORE`, `TOKEN_STORE_UNAVAILABLE`, `TOKEN_STORE_READ_FAILED`, `TOKEN_STORE_WRITE_FAILED`, `TOKEN_STORE_DELETE_FAILED`, `TOKEN_STORE_INVALID`, `TOKEN_STORE_KEY_MISSING`, `TOKEN_STORE_MIGRATION_REQUIRED`, `TOKEN_DECRYPT_FAILED`, `TOKEN_PASSPHRASE_REQUIRED`, `TOKEN_REFRESH_BUSY` |
| OAuth / tenant | `AUTH_REQUIRED`, `OAUTH_AUTHORIZATION_FAILED`, `OAUTH_CALLBACK_INVALID`, `OAUTH_CALLBACK_TIMEOUT`, `OAUTH_CALLBACK_UNAVAILABLE`, `OAUTH_CONFIG_INVALID`, `OAUTH_NETWORK_FAILED`, `OAUTH_REQUEST_FAILED`, `OAUTH_RESPONSE_INVALID`, `OAUTH_REVOKE_FAILED`, `OAUTH_STATE_MISMATCH`, `OAUTH_TOKEN_INVALID`, `TENANT_CONFIG_INVALID`, `TENANT_CONFIG_READ_FAILED`, `TENANT_CONFIG_WRITE_FAILED`, `TENANT_CONTEXT_REQUIRED`, `TENANT_DISCOVERY_FAILED`, `TENANT_NOT_FOUND`, `TENANT_SELECTION_INVALID`, `TENANT_SELECTION_REQUIRED` |
| Create reconciliation | `CREATE_OUTCOME_UNKNOWN`, `CREATE_RECONCILIATION_REQUIRED`, `CREATE_RECONCILIATION_NOT_FOUND`, `CREATE_RECONCILIATION_CONFIRMATION_REQUIRED`, `CREATE_RETRY_MISMATCH`, `CREATE_RETRY_EXPIRED`, `CREATE_STATE_BUSY`, `CREATE_STATE_INVALID`, `CREATE_STATE_INVALID_REQUEST`, `CREATE_STATE_READ_FAILED`, `CREATE_STATE_WRITE_FAILED` |
| Xero API | `XERO_AUTH_FAILED`, `XERO_INSUFFICIENT_SCOPE`, `XERO_NOT_FOUND`, `XERO_RESPONSE_INVALID`, `XERO_TRANSIENT_FAILURE`, `XERO_VALIDATION_FAILED`, `XERO_REQUEST_FAILED`, `ATTACHMENT_READ_FAILED`, `CONTACT_NOT_FOUND`, `CONTACT_AMBIGUOUS` |
| Draft warning/review state | `ITEM_NOT_FOUND`, `ITEM_NOT_PURCHASABLE`, `ITEM_PURCHASE_DETAILS_INCOMPLETE`, `READBACK_MISMATCH`, `MANUAL_INSPECTION_REQUIRED` |

For a partial result, inspect `invoice_id`, observed fields, `final_status`, and `warnings` before taking any retry action. A known draft is deliberately not approved automatically after a failed attachment or approval step. `CREATE_OUTCOME_UNKNOWN` is not a no-create result even though no `invoice_id` was observed.
