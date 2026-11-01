# Windows development build

The current application is built from `rust/`. The previous implementation in
`src/ai_mini_monitor/` and its 0.2.13 version metadata are retained as compatibility
references and regression fixtures, not as the 0.3.0 application executable.

Prerequisites: Windows x64, Rust MSVC toolchain, Windows SDK resource compiler,
and the pinned Codex runtime fetched by `scripts/Fetch-CodexRuntime.ps1`.

```powershell
Set-Location rust
cargo test --offline
cargo clippy --offline --all-targets -- -D warnings
cargo build --release --offline --bins
Set-Location ..
.venv/Scripts/python.exe scripts/package_native.py --output build/native-package
```

Remove `--offline` for the first dependency download. The package includes matching
application source and dependency notices. Signing uses the existing protected
release key outside the checkout; never generate a replacement for an ordinary
release. The fixed online installer is published separately and is not rebuilt
or re-uploaded for an ordinary application update.

Live physical USB and account tests are opt-in. Automated geometry tests do not
replace testing actual Windows scaling or AMD/Intel hardware. Missing readings
remain unavailable rather than becoming zero.
