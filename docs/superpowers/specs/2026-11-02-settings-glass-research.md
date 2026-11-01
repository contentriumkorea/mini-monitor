# Settings visual redesign research — pending direction approval

User request: search the web for high-quality glassmorphism references and redesign
the existing Mini Monitor settings UI. This is not authorization for another public
release or installation. Preserve the existing native application architecture,
monitoring features, account data, and approved black/grey preference.

## Sources actually checked through Aside

- Microsoft, Mica material: https://learn.microsoft.com/en-us/windows/apps/design/style/mica
  - Read primary text and inspected the official dark-theme image.
  - Mica is explicitly opaque, samples wallpaper once, and is intended for
    long-lived application/settings backgrounds. Do not mislabel it as transparent
    glass. Useful reference: separation between base, content layer and title bar.
  - Official image: https://learn.microsoft.com/en-us/windows/apps/design/style/images/materials/mica-dark-theme.png
- Microsoft, Acrylic material: https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic
  - Read primary text including performance, high-contrast and transparency-off
    behavior; inspected the official dark Acrylic image.
  - Translucency/blur creates depth, but rendering is GPU-intensive and can affect
    power/battery. White/neutral text must remain legible. Avoid layering numerous
    independent full-window blur surfaces or promising zero resource cost.
  - Official image: https://learn.microsoft.com/en-us/windows/apps/design/style/images/acrylic-dark-theme-base.png
- Apple, introduces Liquid Glass:
  https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/
  - Read official description of dimensional/translucent materials and updated
    controls. This is a visual-language reference, not an Apple API available to
    this Windows application and not a license to copy its assets.
- Dribbble HR Management Dashboard — Dark Glass UI by Nudoubt Agency:
  https://dribbble.com/shots/26564442-HR-Management-Dashboard-Dark-Glass-UI
  - Page text verified, describing tighter cards and teal/coral colors. Main artwork
    could not be visually verified through the available DOM/capture route; do not
    claim its artwork was inspected or use it as the principal design reference.
  - A separately inspected colorful liquid-glass artwork was not a black/grey
    desktop settings reference and was rejected rather than mislabeled.

Aside delegated research MCP timed out with no retrievable final answer. Recovered
the actual opened public research tabs, directly read sources via Aside REPL, and
viewed the two official PNGs using allowed public same-origin/trusted GETs. CDP
viewport screenshot timed out; direct public-image viewing succeeded. No downloaded
reference artwork is to be bundled into the app.

## Current implementation inspected

`rust/src/settings_window.rs` uses a flat GDI background, uniformly weighted font,
square owner-drawn buttons, standard combo/checkbox/slider controls, fixed grouped
coordinates and a dashboard preview. `options_window.rs` uses a separate generic
form style. Recoloring only the background would leave the principal visual
problems intact. Preserve command IDs, keyboard navigation, DPI handling and
existing ApplicationCore behavior while unifying the two surfaces.

## Candidate directions — not approved yet

1. Recommended: neutral smoke glass. Charcoal single backdrop, fine neutral
   highlights, restrained rounded cards and smaller switches, opaque text/numbers,
   compact three-output row, device/account controls left and preview right. Native
   system material only where supported; readable solid fallback for unsupported
   OS/accessibility/performance conditions. No added WebView or continuous
   decorative rendering. Actual resource and DPI costs must be measured, not assumed.
2. Stronger Liquid Glass appearance: more transparent surfaces, brighter edge
   highlights, greater visual depth. More expressive but requires more care with
   background-dependent contrast and rendering cost. Keep neutral coloring either way.

Proposed scope: main settings plus overlay/taskbar/API preference forms; no change
to the USB-monitor card composition, account/budget calculations, installer or
taskbar-widget visuals unless specifically required by shared settings behavior.
Next step: obtain direction approval, write/approve the short design and plan, then
implement with visual/native interaction and resource regression checks. Current
installed/public version remains 0.3.0; no product source was changed in this research.
