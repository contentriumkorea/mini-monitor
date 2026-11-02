# Mini Monitor 0.3.2 local-PC validation

User selected local installation first. No tag, push or GitHub publication was performed.

## Reproduced and corrected

- Native owner-draw button corner shade22 versus surrounding panel42. Paint the actual parent gradient behind rounded shapes.
- Aliased curves had no fractional coverage; rounded-shape sampling was vertically asymmetric (difference10797). GDI+ anti-aliasing and half-pixel sampling now pass coverage and top/bottom completeness checks.
- Default preview downsampling produced no mixed pixels in a red/blue checker fixture; HALFTONE now preserves blended detail.
- Startup checkbox background22 versus surrounding17; request the parent's background instead of stamping a flat rectangle.
- Button captions used the font cell rather than visible ink. Bounded cached ink measurements center the rendered glyphs, including integer/half-pixel rounding.
- API row controls were positioned near the top of the card; actual row geometry is now centered, with vertically centered single-line labels.
- Compact brightness control height18 was insufficient for the native thumb/track; compact layout reserves24px and keeps the footer accessible.
- Small readout glyphs exposed only16 coverage levels. Cached2x grayscale supersampling passes the finer-edge test without RGB subpixel fringes.
- Large digits and percent ink bottoms differed by4px. Visible-ink placement now shares a common bottom in landscape/portrait fixtures.
- Replace CPU/RAM/GPU/VRAM/API symbols with cached DPI-native geometric UI paths; API uses a code-connection symbol instead of a dollar coin. Existing Codex brand PNG is unchanged.

## Verification

- Rust suite247 passed,24 environment/device/account opt-ins ignored. Clippy all targets, formatting and diff checks passed.
- Read-only review found no Important/Critical issue. Added scope was reviewed separately after the user supplied it; no redundant re-review of the earlier patch.
- Headless agent-desktop synthetic screenshots inspected at960x680 and900x460; compact brightness slider is visible. Synthetic taskbar icon/percentage render inspected at96DPI.
- Existing installed0.3.1 was updated with the normal verified, rollback-capable updater using a locally signed0.3.2 bundle. The local driver requires explicit paths and defaults to verification only; it embeds the existing asInvoker manifest and requests no elevation.
- Final local ZIP SHA256 `8a3e176350e1560af8152959eab445012c99cacd1f09feb29b6e37069f953924`.
- Installed CLI reports0.3.2; installed main executable hash matches the candidate; update journal is `healthy_backup_retained` and startup acknowledgement exists.
- Configuration file hash unchanged across replacement; account status connected and taskbar enabled after restart. Windows app registration updated to0.3.2. Synthetic preview exited gracefully; the real installed app remains running.
- Package explicit inventory313 files; secret/private-key pattern audit found no matches. No user account/configuration files are packaged.

Actual mixed-monitor DPI transitions, OS high-contrast switching, subjective readability on the external USB panel and physical mouse/keyboard interactions were not inferred from these headless checks. No OS display/accessibility settings were changed.
