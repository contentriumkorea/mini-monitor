# Mini Monitor smoke-glass settings

The user selected the recommended neutral smoke-glass direction and explicitly
authorized planning, implementation and publication in one uninterrupted run.
No additional design approval round is needed. Keep the existing native Rust/Win32
application and monitoring behavior; do not introduce a WebView or new UI runtime.

## Visual contract

- Neutral charcoal, never blue/purple accents. Single restrained glass-like base,
  rounded inset cards, fine top highlights, subtle depth and opaque readable text.
- Windows system backdrop/dark title bar/rounded outer corners only through
  documented supported APIs. Unsupported systems keep a matching opaque charcoal
  fallback. Never change OS transparency, accessibility or display settings.
- No continuous decorative animation, screen capture, software desktop-blur loop
  or per-card live blur. Repaint only on exposure, interaction or content changes.
- Main window approximately 960x680 logical pixels: title/subtitle; compact
  three-output cards with switches; left device/account/API groups; right preview
  with per-output settings; compact footer for startup/version/exit.
- Retain all existing commands, safe brightness range, keyboard order, screen
  rotation, output independence, account/API controls and persistence. Glass styling
  applies equally to overlay/taskbar/API preference forms, including their buttons,
  fields, checkboxes and comboboxes. Password controls remain masked.
- Keep approved 9-point semibold control text, add larger title hierarchy, do not
  double-apply OS DPI. All controls fit at supported minimum and high DPI; use
  work-area bounds and compact layout rather than shrink text illegibly.

## Approaches

Chosen: restrained smoke-glass visual language, supported native system material
and solid fallback, preserving native controls/accessibility. Stronger refractive
Liquid Glass was considered but not selected due to contrast/rendering complexity.
Mica is an opaque reference for layering, not claimed to be transparent glass.
Sources and observed limits are recorded in the adjacent settings-glass-research doc.

## Verification and scope

Test geometry, focus/keyboard, every command/checkbox/combo/slider and masked input;
exercise native paint and GDI-resource stability; inspect actual synthetic-data
screens via agent-desktop; compare short full-tree resource measurements. Existing
account files, keys and installation stay untouched during development previews.
Publish the signed 0.3.1 application package and metadata to the existing GitHub
repository after testing. Preserve the fixed installer-v2 binary and direct URLs.
This request does not add an unrelated monitoring feature or installer redesign.
Actual unavailable high-DPI/mixed-monitor hardware remains explicitly unverified.
