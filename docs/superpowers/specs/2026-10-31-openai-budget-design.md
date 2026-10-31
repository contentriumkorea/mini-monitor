# OpenAI API configured-total usage

User-approved scope: no browser bridge. Manually configured USD total and UTC start date; official organization Costs API automatically supplies spending. Display spending / configured total as usage percentage, separately from Codex. This is not a credit balance, credit ledger, or monthly budget reset.

## Approach

- Chosen: official `GET /v1/organization/costs` using a user-entered organization Admin API key. No inference calls, token-count price estimates, browser sessions, or account changes.
- Rejected: browser billing-screen integration (user explicitly declined); claiming actual prepaid balances from Costs API (not documented).
- Preserve Python/Tk/Pillow, existing Codex authentication, physical-device transport, overlay transparency, and taskbar positioning.

## Contract

- USD total > 0, at most two decimal places, maximum 999999.99. Start date YYYY-MM-DD at 00:00 UTC, not in the future. Accumulate to current time; no automatic monthly reset or total adjustment. Settings explain organization-wide scope and UTC.
- Costs client uses Decimal, daily buckets, bounded pagination, HTTPS with redirect refusal. Reject malformed/unsupported currency/non-finite/negative amounts rather than fabricate zero. Empty valid results mean zero. Above-budget values remain >100% in text; gauge caps at 100%.
- Read once on enabled startup, then every 600 seconds. Manual refresh is rate-limited to 60 seconds. A single background worker; no network on Tk thread. Disable/changed settings invalidate in-flight results. Errors and pending states show unavailable percentage, last-success time retained only for context.
- Key entry masked and never pre-populated; only Windows DPAPI store persists secrets. JSON configuration stores only enabled flag, total and start date. No secrets in test fixtures, logs, screenshots, repository or deliverables.
- Optional OPENAI API card: when enabled, dashboard and overlay have six cards; otherwise original five-card layout is unchanged. Settings show totals/spending/percentage and a separate compact configuration dialog. Taskbar API item is user-selectable, not silently added to existing selections. No graphs or new background decorations.
- Initial configuration disabled with no account-specific amounts hard-coded. Admin key must be entered by user through the app; do not obtain/create one on their behalf.
- No deployment or replacement of installed app in this request. Verify source, rendered previews, isolated GUI, test suite. Live-account API verification awaits user key if no app-owned key is already configured; do not inspect unrelated secrets.

## Verification

Network fixtures: correct endpoint/UTC dates, exact decimals, pages, empty costs, malformed data, mixed currency, 401/403/429, redirect refusal. Lifecycle: disabled service no requests, interval admission, settings change during fetch, shutdown. Display: unavailable != 0%, >100%, totals intact, old config roundtrip, Codex and API simultaneously, both orientations and taskbar selection. Secret persistence: DPAPI roundtrip in temporary path only. Actual account credentials never appear in QA artifacts.

Official reference: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
