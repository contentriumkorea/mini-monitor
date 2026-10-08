# OpenAI Codex app-server provenance

Mini Monitor bundles the unmodified, official standalone Windows x64 app-server
from [OpenAI Codex rust-v0.161.0](https://github.com/openai/codex/releases/tag/rust-v0.161.0).
`RUNTIME.json` pins both the release ZIP and extracted executable by size and SHA-256.
The large executable is intentionally excluded from Git; `scripts/Fetch-CodexRuntime.ps1`
downloads and verifies it before a release build. The application re-verifies the
executable hash before launch. The Mini Monitor build includes it at
`third_party/codex-app-server/codex-app-server.exe`.

The complete corresponding upstream source is at
https://github.com/openai/codex/tree/rust-v0.161.0 . This binary is Apache-2.0;
the full Apache 2.0 terms are included in `LICENSES/python/cryptography/LICENSE.APACHE`,
and the upstream NOTICE is reproduced in `NOTICE`. Mini Monitor itself remains
GPL-3.0-or-later. The app-server is a separate subprocess, not linked into
Mini Monitor's Python code.

The app supplies its own isolated `CODEX_HOME`. It does not import another
Codex application's local authentication or session data. Login is initiated
by the official app-server account API and completed by the user in a browser.
