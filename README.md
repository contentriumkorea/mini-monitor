# Mini Monitor

Mini Monitor is an independent, unofficial GPL-3.0-or-later Windows dashboard, not an OpenAI product. It supports a 3.5-inch USB serial display and an optional desktop overlay. It shows CPU, GPU, RAM and rate-limit information obtained through the official Codex CLI. The four cards work in 480×320 landscape or 320×480 portrait orientation.

[한국어 설치·사용 안내](README_KO.md) · [Releases](https://github.com/contentriumkorea/mini-monitor/releases) · [Source offer](SOURCE-OFFER.md) · [Test results](TEST_RESULTS.md)

![Synthetic landscape dashboard](previews/native-refresh/normal.png)

Download the stable `Mini-Monitor.zip` release and keep its entire `Mini-Monitor` folder together. Run `Mini-Monitor.exe` to open setup without starting serial transmission. `Mini-Monitor-CLI.exe` provides read-only diagnostics and synthetic preview generation. The official [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) must be installed separately; Mini Monitor neither bundles it nor copies your default Codex authentication. The Windows executable is not Authenticode-signed; the signed update metadata is a separate integrity mechanism.

Only devices matching USB VID `1A86`, PID `5722` and serial `USB35INCHIPSV2` may receive serial output. An older AI-Mini-Monitor build needs one manual baseline upgrade. Thereafter, Mini Monitor checks for stable updates at startup and periodically while running, with a 24-hour network-check gate; the setup button can check on demand. An update notice never downloads or installs a release by itself: applying it requires an explicit click. Physical USB output and live account login require separate testing on your own hardware/account.
