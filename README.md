# Mini Monitor

Mini Monitor is a GPL-3.0-or-later Windows dashboard for a supported 3.5-inch USB serial display, with an optional desktop overlay. It shows CPU, GPU, RAM and official Codex account rate-limit information. The four cards work in 480×320 landscape or 320×480 portrait orientation.

[한국어 설치·사용 안내](README_KO.md) · [Releases](https://github.com/contentriumkorea/mini-monitor/releases) · [Source offer](SOURCE-OFFER.md) · [Test results](TEST_RESULTS.md)

![Synthetic landscape dashboard](previews/native-refresh/normal.png)

Download the stable `Mini-Monitor.zip` release and keep its entire `Mini-Monitor` folder together. Run `Mini-Monitor.exe` to open setup without starting serial transmission. `Mini-Monitor-CLI.exe` provides read-only diagnostics and synthetic preview generation. The official [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) must be installed separately; Mini Monitor neither bundles it nor copies your default Codex authentication. The Windows executable is not Authenticode-signed; the signed update metadata is a separate integrity mechanism.

Only devices matching USB VID `1A86`, PID `5722` and serial `USB35INCHIPSV2` may receive serial output. An older AI-Mini-Monitor build needs one manual baseline upgrade; subsequent updates can be checked and applied with explicit user action. Physical USB output and live account login require separate testing on your own hardware/account.
