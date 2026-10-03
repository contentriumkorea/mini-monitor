# AI Mini Monitor

AI Mini Monitor는 Windows의 CPU, GPU, 시스템 메모리와 선택한 AI 사용 정보를 TURZX/Turing 계열 3.5인치 USB 직렬 화면에 표시하는 브랜드 비종속 애플리케이션입니다. 같은 대시보드를 PC 바탕화면 위의 선택적 상시 표시 상태창으로도 볼 수 있습니다. 장치의 320×480 패널을 가로 480×320 또는 세로 320×480으로 사용할 수 있으며, 디스크·네트워크·날씨·볼륨·MSI 정보는 수집하거나 표시하지 않습니다.

지원 범위는 의도적으로 좁습니다. 다음 세 식별자가 모두 정확히 일치하는 장치에만 직렬 데이터를 보냅니다.

- USB VID: `1A86`
- USB PID: `5722`
- USB 일련번호: `USB35INCHIPSV2`

COM 번호는 고정하지 않습니다. 현재 시험 장치는 COM3이지만, 재연결 뒤 번호가 바뀌면 다시 탐지합니다. `manual_port`를 지정해도 위 세 식별자 검사는 생략되지 않습니다.

> 중요: 2026-08-10 17:29 KST 실행에서 COM3 열기가 Windows `AccessDenied`로 실패해 실제 전송 바이트가 0이었지만, 당시 배포본은 writer 스레드 시작만으로 `RUNNING`을 표시했습니다. 현재 소스는 포트 설정과 시작 명령, 첫 전체 프레임 쓰기까지 완료된 뒤에만 시작 성공을 표시하도록 수정됐습니다. Codex 일반 한도와 named Spark 한도를 혼동하던 문제 및 PC 상태창의 전체 캔버스·격자 제거까지 포함한 fresh onedir와 ordered 전체 검증은 **`699 passed, 0 skipped`**, 빌드 뒤 패키징·라이선스 검증은 `17 passed, 0 skipped`입니다. 새 artifact는 1,412개 파일이며 각 파일의 확정 SHA-256은 배포 폴더의 `SHA256SUMS.txt`에 기록됩니다. packaged CLI 버전·읽기 전용 진단·미리보기 9개와 no-serial·최소화 상태창 smoke도 통과했습니다. 상태창 smoke는 카드 불투명도 65%·크기 100%에서 종료 코드 0, 잔여 프로세스 0이었고, [`build/overlay-grid-free-packaged-smoke.png`](build/overlay-grid-free-packaged-smoke.png)에서 전체 캔버스·격자 미표시, 반투명 카드 면, 선명한 전경을 확인했습니다. 물리 LCD·실제 밝기 변화·실제 COM, 실제 Windows sleep/resume·USB detach/reconnect, 실제 시작 프로그램 registry 쓰기, 실제 다른 PC와 혼합 DPI 상태창 시각 검증은 대기 중입니다. 자세한 구분은 [DEVICE_BENCHMARK.md](DEVICE_BENCHMARK.md)와 [TEST_RESULTS.md](TEST_RESULTS.md)를 확인하십시오.

> 이전 `codex-85-packaged-smoke.png`와 `final-status-toggle-zero` 캡처는 화면 전체 캔버스·격자를 제거하기 전 역사적 기록입니다. 현재 배포본의 격자 제거 근거는 `overlay-grid-free-packaged-smoke.png`와 현재 `BUILD-INFO.json`·`SHA256SUMS.txt`입니다.

## 주요 동작

- 센서 샘플은 원시값과 표시값을 분리하고 시간 기반 EMA로 부드럽게 표시합니다.
- 일반 GUI를 실행하면 먼저 별도의 설정/제어 창만 열립니다. `모니터 시작` 전에는 COM 포트를 열거나 패널에 전송하지 않습니다.
- 설정 창의 내장 미리보기는 선택한 가로·세로 framebuffer를 비율 그대로 맞춰 표시합니다. 세로 320×480은 480×320 미리보기 영역 안에 aspect-fit됩니다. 별도 일반 미리보기 창은 제공하지 않습니다.
- 내장 미리보기 바로 아래의 Primary 버튼은 실제 상태에 따라 정확히 `PC 상태창 켜기` 또는 `PC 상태창 끄기`로 바뀝니다. 누르면 상태창을 즉시 표시하거나 숨깁니다. 실패하면 가능한 경우 저장값과 실제 창을 이전 확정 상태로 원복하고, 원복할 수 없으면 관찰된 실제 상태에 버튼과 설정을 동기화합니다. 오류 피드백 영역은 미리 고정되어 메시지가 생겨도 아래 버튼이나 창 높이가 밀리지 않습니다.
- `PC 상태창 설정`은 표시 토글과 분리된 별도 설정 창이며, 트레이에는 `PC 상태창 켜기/끄기` 메뉴가 있습니다. 두 경로 모두 같은 상태창 한 개를 제어합니다.
- PC 상태창은 제목 표시줄 없는 상시 최상단 창입니다. 마우스로 화면 어디든 끌어 놓고, 상태창에 포커스가 있을 때 `Alt`+방향키로 위치를 조정할 수 있습니다. 크기 50~200%, 연결부와 4개 카드 면의 불투명도 0~100%와 위치를 저장합니다. 화면 전체 캔버스와 격자는 표시하지 않으며 semantic RGBA에서는 해당 영역의 alpha가 정확히 0입니다. 글자·숫자·게이지·그래프의 내부 foreground alpha는 항상 255를 유지합니다.
- `장치 찾기`는 COM을 열지 않는 읽기 전용 열거이며 VID/PID/일련번호가 모두 일치하는 포트만 준비 상태로 표시합니다.
- UI 렌더링은 기본 12Hz, USB 제출은 기본 5Hz이며 각각 10–15Hz, 1–8Hz 범위로 제한됩니다.
- CPU·GPU·시스템 RAM에는 0~100% 가로 게이지가 있으며 `NORMAL`, `CAUTION`, `WARNING`, `NO DATA` 문구와 패턴으로 색상 외에도 상태를 구분합니다. CPU·GPU·RAM·AI 카드의 큰 수치는 JetBrains Mono ExtraBold로 표시합니다.
- CPU·GPU·RAM·AI 카드 제목은 18px Inter ExtraBold 흰색 텍스트를 1차 식별자로 사용합니다. 2px 외곽선과 카드별 4px rail, 가로 화면의 2px 밑줄은 작은 화면에서 카드 경계를 보강하는 보조 단서입니다.
- CPU와 GPU 카드에는 모델명을 보조 정보로 표시합니다. 모델명이 길어도 수치·게이지·그래프의 위치는 바뀌지 않으며, 카드 폭을 넘는 부분만 `...`로 줄입니다.
- 변경된 위젯 영역만 RGB565LE 부분 패치로 전송합니다.
- 직렬 포트는 단일 writer 스레드만 소유하며 대기열은 최대 1건, 최신 프레임 우선입니다.
- `모니터 시작` 때마다 새 controller를 만들고, `모니터 중지`가 완료되면 COM·센서·AI 수집과 해당 controller를 폐기합니다. 다음 Start는 이전 객체를 재사용하지 않습니다.
- `모니터 시작` 성공은 정확한 장치 선택·재검증과 115200 baud, 8-N-1, `Handshake.None`, DTR/RTS ON 포트 열기 → 저장된 밝기 1~50%(기본 25%) → SCREEN_ON → 100ms 안정화 → 선택한 방향 → 500ms 안정화 → 최신 밝기 재확인 → 선택한 크기의 첫 전체 프레임 쓰기 → `ONLINE` 순서를 제한 시간 안에 완료한 뒤에만 표시합니다. 재연결과 절전 복귀도 저장된 방향·활성 크기·최신 밝기를 다시 적용합니다. 이 장치는 프레임 ACK를 제공하지 않으므로 실제 LCD 픽셀 표시 완료를 뜻하지는 않습니다.
- 설정 창의 화면 밝기는 1~50%, 기본 25%입니다. 슬라이더는 120ms debounce로 마지막 값을 모아 처리하며, 정지 중에는 설정만 저장하고 실행 중에는 기존 단일 serial writer에 live 적용합니다. 잘못된 값은 저장·전송 전에 거부하고 처리 실패 시 마지막 확정값으로 되돌립니다. 밝기 변경을 위해 두 번째 COM 연결이나 별도 writer를 만들지 않습니다.
- 허용된 명령은 밝기 `0x6E`, 화면 켜기 `0x6D`, 방향 `0x79`, 비트맵 `0xC5`뿐입니다. reset, hello, screen-off, clear, firmware 명령이나 일반 임의 명령 API는 없습니다.
- 제조사 Run 정적 호출 흐름은 최대 밝기에 해당하는 raw 0 → SCREEN_ON → 약 100ms 뒤 렌더 순서였습니다. 현재 앱은 검은 종료 상태를 해제하는 시작 명령은 유지하되 눈부심과 예기치 않은 출력을 줄이기 위해 검증된 1~50% 범위만 허용하고 기본값을 25%로 둡니다.
- 연결이 끊기거나 COM 번호가 바뀌면 엄격한 장치 재검증 뒤 전체 프레임을 복원합니다.
- 다른 프로그램이 COM을 사용 중이면 반복 open을 멈추고 설정 창에 `PORT IN USE`를 표시합니다. UsbMonitor와 다른 직렬 앱을 알림 영역까지 완전히 종료한 뒤 다시 시작해야 하며, 앱이 해당 프로세스를 자동 종료하지 않습니다.
- 알림 영역에서 상태 확인, `PC 상태창 켜기/끄기`, 장치 재연결, 종료를 할 수 있습니다.
- 세션당 한 인스턴스만 실행합니다. 이미 실행 중인 프로세스를 강제로 종료하지 않습니다.
- 1024×720급 작업 영역에서도 Win32 모니터별 `rcWork`와 제목 표시줄·테두리 크기를 기준으로 설정 창을 맞춥니다. 왼쪽 설정 카드만 수직 스크롤하고 `모니터 시작`·`모니터 중지`·`프로그램 종료`·자동 실행 제어는 고정 푸터에 남습니다. 키보드 포커스가 숨은 항목으로 이동하면 자동으로 보이게 스크롤하며, 마우스 휠은 왼쪽 스크롤 영역 안에서만 처리합니다.
- 시작 프로그램 등록은 기본 꺼짐이며, 설정 창의 하나의 `ttk.Checkbutton`을 사용자가 직접 선택한 경우에만 현재 사용자 계정에 등록합니다.

## 시스템 요구사항

- 64비트 Windows 10 또는 Windows 11
- 위 식별자와 정확히 일치하는 `USB35INCHIPSV2` 장치
- Windows USB 직렬 드라이버
- 센서 라이브러리 실행이 가능한 .NET Framework 환경. 현재 검증 PC는 .NET Framework 4.8.1입니다.
- Codex 로컬 한도는 별도 로그인·API 키·인터넷 연결 없이 읽을 수 있지만, 이 PC에 Codex가 만든 지원 형태의 로컬 세션 기록이 있어야 합니다.
- OpenAI 조직 사용량을 표시하려면 해당 조직 Usage/Costs를 읽을 수 있는 OpenAI Admin Key와 인터넷 연결

패키지에는 Python 런타임이 포함되므로 일반 사용자는 Python을 별도로 설치할 필요가 없습니다. 현재 배포 파일은 Authenticode 코드 서명이 없으므로 Windows SmartScreen이 경고할 수 있습니다. `SHA256SUMS.txt`는 복사 중 손상이나 기준 해시와의 불일치를 확인하는 용도이며, 그 파일 자체도 서명되지 않았으므로 배포자 신원이나 파일 출처를 인증하지 않습니다. 신뢰할 수 있는 별도 전달 경로에서 받은 해시 또는 서명된 배포 채널과 대조할 수 없으면 실행하지 마십시오.

## 설치와 실행

### 패키지 사용

1. 제조사 UsbMonitor와 COM 포트를 사용하는 다른 프로그램을 알림 영역까지 완전히 종료합니다.
2. `dist\AI-Mini-Monitor` 폴더 전체를 원하는 위치로 복사합니다. 이 배포본은 onedir 방식이므로 EXE만 분리하면 실행되지 않습니다.
3. 기본 GUI 실행 파일 `dist\AI-Mini-Monitor\AI-Mini-Monitor.exe`를 실행합니다.
4. 일반 실행에서는 설정/제어 창과 선택한 방향의 내장 미리보기만 표시됩니다. 이 단계의 자동 장치 확인과 `장치 찾기`는 읽기 전용이며 COM을 열지 않습니다.
5. AI 사용량 방식을 선택합니다. Codex 로컬 한도는 체크박스로 로컬 읽기에 명시적으로 동의해야 하며, 원하지 않으면 `사용 안 함`을 선택할 수 있습니다.
6. `화면 보기`에서 `가로` 또는 `세로`를 고르고 필요하면 `상하 반전 (180°)`을 선택합니다. 실제 패널 전송은 `모니터 시작`을 누른 뒤에만 시작되며, 위 초기화와 첫 전체 프레임 쓰기가 끝나기 전에는 실행 성공으로 표시하지 않습니다. `모니터 중지`를 누르면 COM과 모든 수집 작업을 닫습니다.
7. Windows 로그인 때 저장된 설정으로 자동 실행하려면 고정 푸터의 `Windows 시작 시 자동 실행` 체크박스를 선택합니다. 다시 누르면 같은 체크박스로 해제합니다.
8. PC 화면 위에도 같은 상태를 보려면 내장 미리보기 아래의 `PC 상태창 켜기`를 누릅니다. 표시 중에는 같은 버튼이 `PC 상태창 끄기`로 바뀝니다. 트레이의 `PC 상태창 켜기/끄기`로도 즉시 전환할 수 있습니다.
9. 크기·연결부 및 4개 카드 면 불투명도·위치 초기화는 별도의 `PC 상태창 설정`에서 조정합니다.

배포 폴더의 두 실행 파일은 역할이 다릅니다.

- `AI-Mini-Monitor.exe`: 콘솔 창 없이 실행되는 setup-first 설정/제어·트레이 프로그램
- `AI-Mini-Monitor-CLI.exe`: 진단, 보안 키 설정, 미리보기 생성, 시작 프로그램 관리용 콘솔 프로그램

PowerShell 예시:

```powershell
# 일반 실행: 설정/제어 창을 열며 ‘모니터 시작’ 전에는 COM을 열지 않음
.\AI-Mini-Monitor.exe

# 저장된 설정으로 즉시 시작하는 명시적 최소화/자동 시작 경로
.\AI-Mini-Monitor.exe --minimized

# 읽기 전용 진단: 포트와 센서를 열람하지만 직렬 포트는 열지 않음
.\AI-Mini-Monitor-CLI.exe --diagnose .\diagnostics.json

# 합성 데이터 미리보기. 실장치로 전송하지 않음
.\AI-Mini-Monitor-CLI.exe --preview normal
```

프로젝트 폴더에서는 같은 작업을 래퍼 스크립트로 실행할 수 있습니다.

```powershell
.\scripts\Run.ps1
.\scripts\Run.ps1 -Cli -AppArguments '--diagnose', '.\diagnostics\current.json'
.\scripts\Render-Previews.ps1
```

## 설정

기본 설정 파일은 `%LOCALAPPDATA%\AI-Mini-Monitor\config.json`입니다. 파일이 없으면 안전한 기본값을 사용합니다. `config.example.json`을 복사해 수정할 수 있으며, 설정을 바꾼 뒤에는 애플리케이션을 다시 시작하십시오.

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\AI-Mini-Monitor" | Out-Null
Copy-Item .\config.example.json "$env:LOCALAPPDATA\AI-Mini-Monitor\config.json"
notepad "$env:LOCALAPPDATA\AI-Mini-Monitor\config.json"
```

설정의 핵심 값은 다음과 같습니다.

| 항목 | 기본값 | 허용 범위 또는 의미 |
|---|---:|---|
| `device.auto_detect` | `true` | 동적 COM 탐지 |
| `device.manual_port` | `null` | 특정 COM으로 검색 범위를 좁힘. 장치 식별 검사는 그대로 수행 |
| `device.usb_fps` | `5.0` | 1–8Hz |
| `device.brightness` | `25` | 검증된 1~50% 화면 밝기 |
| `device.rotation` | `landscape` | `landscape`, `landscape_inverted`, `portrait`, `portrait_inverted` |
| `overlay.enabled` | `false` | PC 상시 표시 상태창 사용 여부 |
| `overlay.opacity` | `0.85` | 상태창 연결부와 4개 카드 면 불투명도 0.0~1.0. 화면 전체 캔버스·격자는 미표시, 전경 내부 alpha는 255 유지 |
| `overlay.scale_percent` | `100` | 원본 framebuffer 기준 50~200% |
| `overlay.x`, `overlay.y` | `null` | 저장된 화면 좌표. 보조 모니터는 음수 좌표를 사용할 수 있음 |
| `sensors.sample_interval_ms` | `500` | 250–2000ms |
| `app.render_fps` | `12.0` | 10–15Hz |
| `ai.provider` | `not_configured` | `not_configured`, `codex_local`, `openai_api`, `chatgpt_activity` |
| `ai.codex_local_consent` | `false` | Codex 로컬 세션 한도 숫자 읽기에 대한 명시적 동의 |
| `ai.usage_refresh_seconds` | `60` | OpenAI Usage 조회, 최소 60초 |
| `ai.cost_refresh_seconds` | `600` | OpenAI Costs 조회, 최소 600초 |

`daily_budget_usd`와 `monthly_budget_usd`는 화면에 사용 비율을 표시하기 위한 로컬 비교 기준입니다. OpenAI 결제를 차단하거나 플랫폼 예산 한도를 변경하지 않습니다.

설정 창에서는 네 개의 내부 값을 `화면 보기: 가로/세로`와 `상하 반전 (180°)` 조합으로 선택합니다. 가로는 480×320, 세로는 320×480 레이아웃을 사용합니다. 반전은 호스트에서 완성 이미지를 회전시키는 방식이 아니라 장치의 방향 명령으로 적용하므로 글자와 레이아웃을 이중 회전하지 않습니다. 실행 중에는 방향 설정을 잠그며, 변경하려면 먼저 `모니터 중지`를 누른 뒤 다시 시작해야 합니다.

같은 장치 카드의 `화면 밝기` 슬라이더는 1~50% 범위이며 기본값은 25%입니다. 움직이는 동안 120ms debounce로 최신 선택만 제출합니다. 모니터가 중지된 상태에서는 검증한 값을 설정에만 저장하고, 실행 중에는 현재 controller가 소유한 단일 writer에만 전달합니다. 최신 저장값은 첫 시작·재연결·절전 복귀 때 다시 적용됩니다. 범위를 벗어나거나 정수가 아닌 값은 저장 전에 거부하고 저장 또는 live 적용 작업이 실패하면 UI를 마지막 확정값으로 되돌립니다. 이 경로는 reset·hello·screen-off 명령을 추가하거나 두 번째 COM handle을 열지 않습니다.

`사용량 확인` 결과 영역은 클릭 전에 두 줄 높이를 미리 확보합니다. 확인 중·완료·오류 문구가 바뀌어도 설정 창과 버튼 위치가 움직이지 않으며, 상세 정보는 카드 전체 폭에서 두 줄로 정리됩니다. 100%·125%·150% 배율에서 `프로그램 종료` 버튼이 창 밖으로 밀리지 않는지 회귀 시험합니다.

`Codex 로컬 한도` 동의와 `상하 반전 (180°)`은 네이티브 `ttk.Checkbutton`의 포커스·Space 키 전환·비활성화 의미를 유지합니다. 표시만 clam 테마의 `X` 대신 미선택은 선명한 빈 상자, 선택은 cyan `✓`로 통일했으며 100%·125%·150% 배율에서 키보드 전환과 disabled 상태를 회귀 시험합니다.

### PC 데스크톱 상시 표시 상태창

설정 창 메인의 Primary 버튼은 마지막 확정 상태를 읽어 `PC 상태창 켜기` 또는 `PC 상태창 끄기`로 표시합니다. 클릭 즉시 실제 상태창과 저장 상태를 함께 바꾸며 적용 중에는 중복 요청을 막습니다. 실패하면 가능한 경우 양쪽을 이전 확정 상태로 원복하고, 원복이 불가능하면 관찰된 실제 상태에 버튼과 설정을 동기화합니다. 버튼 아래에는 두 줄 오류 피드백 높이를 항상 확보하므로 실패 문구가 나타나도 `PC 상태창 설정`이나 `장치 다시 연결` 버튼이 밀리지 않습니다.

`PC 상태창 설정`은 본 설정 창의 크기를 늘리지 않는 별도 설정 창입니다. 여기서 표시 여부, 크기 50~200%, 연결부와 4개 카드 면의 불투명도 0~100%와 저장 위치를 조정할 수 있습니다. 트레이의 `PC 상태창 켜기/끄기`도 같은 표시 상태를 전환합니다. 화면 전체 캔버스와 격자는 오버레이에서 완전히 제거하며 semantic RGBA의 바깥 영역은 alpha 0입니다. 선택한 불투명도는 연결부와 4개 카드 면에만 적용하고 **글자·숫자·게이지·그래프의 내부 foreground alpha는 항상 255**로 유지합니다. 물리 LCD용 RGB framebuffer에는 기존 격자가 그대로 남습니다.

Windows에서는 하나의 제목 없는 layered HWND를 `UpdateLayeredWindow`의 `ULW_ALPHA` 방식으로 합성합니다. 연결부와 4개 카드 면에만 선택한 alpha를 적용하고 글자·숫자·게이지·그래프의 내부 픽셀은 alpha 255를 유지하며, 글자와 선 가장자리만 자연스러운 anti-alias alpha를 사용합니다. 화면 전체 캔버스와 격자는 합성하지 않습니다. 다만 Windows에서 alpha 0 픽셀은 hit-test가 사라지므로 전체 사각형 드래그를 유지하기 위해 native 창에 RGB가 `(0, 0, 0)`인 무색 1/255 alpha 입력 면만 둡니다. 이 native 입력 면에는 격자 RGB가 없어 시각적 잔상이 생기지 않으며, 저장된 불투명도 0%는 카드 면에 정확히 0.0으로 적용됩니다.

상태창은 제목 표시줄과 테두리가 없고 항상 다른 일반 창 위에 표시됩니다. 상태창 아무 곳이나 왼쪽 버튼으로 끌어 이동할 수 있으며, 상태창에 포커스가 있을 때 `Alt`+방향키로 미세 조정할 수 있습니다. 전역 단축키나 마우스 훅을 설치하지 않으므로 다른 프로그램에 포커스가 있을 때 키 입력을 가로채지 않습니다. 위치는 Windows 가상 화면 좌표로 저장하므로 왼쪽이나 위쪽 보조 모니터의 음수 좌표도 유지합니다. 이동·크기 변경 뒤에는 해당 모니터의 작업표시줄을 제외한 사용 가능 영역 안으로 맞추고, 저장된 모니터가 제거되었거나 해상도 배치가 바뀌면 현재 유효한 화면으로 복구합니다.

가로는 480:320, 세로는 320:480 비율을 유지해 확대·축소하므로 수치와 카드가 찌그러지지 않습니다. 표시 중에는 미니 모니터와 동일한 렌더 프레임을 갱신하고, 숨긴 상태에서는 프레임마다 PC 상태창용 이미지 크기 변경을 수행하지 않습니다. `모니터 중지` 뒤에는 마지막 프레임 또는 시작 전 정적 설정 프레임을 유지하며, CPU·GPU·RAM·AI 실시간 값은 다시 `모니터 시작`한 뒤 갱신됩니다.

이 상태창을 표시하거나 이동하는 작업은 COM 포트를 열지 않고 USB 패킷을 보내지 않습니다. 자동 시작 기능과 달리 레지스트리를 변경하지 않으며, 전역 키보드·마우스 훅이나 별도 백그라운드 serial writer도 만들지 않습니다. 상태창 설정과 저장 위치는 일반 JSON 설정에만 기록됩니다.

실제 source desktop no-serial 통합 실행은 종료 코드 0이었고, per-pixel 결과는 [`build/qa-overlay-per-pixel/source-smoke.png`](build/qa-overlay-per-pixel/source-smoke.png)에 저장했습니다. 실제 layered-window 갱신 200회에서 GDI object 증가는 0이었으며 평균 합성 시간은 100% 크기 1.377ms/frame, 200% 크기 17.661ms/frame이었습니다. 이 시험은 COM을 열지 않았습니다. 물리 LCD용 RGB 결과도 가로 `a88743bb68c6cad961c3f0e0f17a80a040516e842c545d850b6786394c448f55`, 세로 `05dcd9a44d50dbc2fd69c2a357f45a8aba9b0b689f42e8af07be902c0a32e091`로 이전과 정확히 같아 오버레이 alpha 합성이 USB framebuffer를 바꾸지 않음을 확인했습니다.

현재 packaged GUI는 카드 불투명도 65%, 크기 100%, 최소화, no-serial 설정으로 상태창 smoke를 완료했고 종료 코드와 잔여 프로세스가 모두 0이었습니다. 실제 Win32 캡처 [`build/overlay-grid-free-packaged-smoke.png`](build/overlay-grid-free-packaged-smoke.png)에서 화면 전체 캔버스·격자가 사라지고 연결부와 4개 카드 면만 반투명하며 전경은 선명한 것을 확인했습니다. 이전 [`build/final-status-toggle-zero/packaged-overlay-zero-clean.png`](build/final-status-toggle-zero/packaged-overlay-zero-clean.png)과 652-test 패키지의 35% smoke는 격자 제거 전 역사적 증거입니다. 어느 smoke도 물리 LCD 검증을 대신하지 않습니다.

### 센서 선택

CPU 사용률과 시스템 RAM은 `psutil`에서 읽고, CPU/GPU 온도와 GPU 부하는 LibreHardwareMonitor에서 읽습니다. LibreHardwareMonitor는 CPU와 GPU만 활성화하며 저장장치, 네트워크, 메모리, 메인보드 등 다른 하드웨어 poller는 활성화하지 않습니다.

CPU 모델명은 Windows의 `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString`을 읽기 전용으로 한 번만 조회하고, 읽을 수 없을 때만 운영체제의 processor 문자열을 fallback으로 사용합니다. GPU 모델명은 부하 또는 온도에 실제로 선택된 동일 LibreHardwareMonitor 센서의 하드웨어 이름을 사용합니다. Parsec·가상 GPU·Remote Desktop·간접 디스플레이·Microsoft 기본 렌더러는 자동 선택과 수동 식별자 선택 모두에서 제외합니다. 두 이름은 제어 문자를 제거하고 공백을 한 줄로 정규화한 뒤 최대 64자로 제한하며, 화면에서는 10px 또는 9px Bold로 맞춘 뒤 필요한 경우에만 ASCII `...`를 붙입니다. 모델명이 없거나 길어도 퍼센트·온도·게이지·그래프 좌표는 이동하지 않습니다.

자동 선택이 원하는 GPU를 고르지 못하면 먼저 읽기 전용 진단을 생성하십시오.

```powershell
.\AI-Mini-Monitor-CLI.exe --diagnose .\diagnostics.json
```

`candidate_sensors`에서 값이 실제로 존재하는 식별자를 확인한 뒤 설정 파일에 그대로 넣습니다.

```json
{
  "sensors": {
    "sample_interval_ms": 500,
    "cpu_temperature_sensor": null,
    "gpu_load_sensor": "/gpu-nvidia/0/load/0",
    "gpu_temperature_sensor": "/gpu-nvidia/0/temperature/0"
  }
}
```

위 GPU 식별자는 현재 시험 PC의 예시일 뿐이며 PC마다 다를 수 있습니다. 존재하지 않는 식별자를 지정하면 임의의 센서로 대체하지 않고 `--`와 사유를 표시합니다.

현재 시험 PC의 Intel Core i7-14700K는 비관리자 환경에서 CPU 온도 센서 이름은 보고됐지만 값은 제공되지 않았습니다. 따라서 CPU 온도가 `--`로 표시되는 것이 현재의 안전한 정상 동작입니다. 이 앱은 온도값을 얻기 위해 저수준 드라이버를 설치하거나 UAC 승격, PawnIO/WinRing 설치를 시도하지 않습니다. GPU 부하와 온도, CPU 사용률, RAM은 정상 수집됐습니다.

### 0~100% 하드웨어 게이지

CPU, GPU, 시스템 RAM 카드는 고정된 트랙 안에 0~100% fill을 표시합니다. CPU/GPU 트랙은 가로 206×9px·세로 282×9px이고, RAM 트랙은 가로 206×12px·세로 282×12px입니다. AI 사용량 바는 가로 206×10px·세로 282×9px입니다. 숫자 퍼센트의 고정 앵커와 tabular 숫자는 유지하며, 값이 없거나 유효하지 않으면 fill 대신 패턴과 `NO DATA`를 표시합니다.

큰 값이 순수한 퍼센트 형식일 때는 ExtraBold 숫자와 오른쪽 아래의 `%`를 분리해 그립니다. CPU/GPU 숫자는 가로 42px·세로 38px, RAM 숫자는 가로 44px·세로 40px를 기준으로 합니다. 일반 AI 퍼센트는 가로 40px·세로 34px이지만, CODEX 남은 한도는 RAM과 같은 상대 bbox·오른쪽 anchor·글꼴을 사용해 가로 44px·카드 기준 `(112, 83)`, 세로 40px·`(112, 69)`에 고정합니다. `%`는 숫자 절반 크기이며 보이는 하단 기준선을 숫자와 맞춥니다. CODEX의 `0%`, `9%`, `10%`, `78%`, `99%`, `100%`, `--`와 실제 상태를 조합한 684건에서 텍스트 clip과 수치/라벨/바 충돌은 0건입니다. `--`, 비용, 기간, `READY`처럼 순수한 퍼센트가 아닌 값은 축소 기호를 붙이지 않고 전체 크기의 단일 문자열로 유지합니다. 실시간 sparkline은 대체로 카드 면적의 약 8~10%만 차지하도록 줄였고, 작은 `30S`·`60S` 시간 표시는 제거해 수치와 게이지를 우선합니다.

- 0% 이상 80% 미만: `NORMAL`
- 80% 이상 95% 미만: `CAUTION`
- 95% 이상: `WARNING`
- 값 없음: `NO DATA`

상태 문구와 값 없음 패턴을 함께 사용하므로 길이와 색상만으로 상태를 판단할 필요가 없습니다.

### 3.5인치 카드 제목 가독성

480×320 패널을 대각선 3.5인치로 보면 약 164.8ppi이며 1px은 약 0.154mm입니다. 기존 카드 제목은 11px Inter SemiBold의 동일한 회색이었습니다. 현재 `CPU`, `GPU`, `RAM`, AI 카드의 기준 제목은 18px Inter ExtraBold와 흰색 `#F4F7FB`를 사용해 작은 실물 화면에서 항목 구분을 우선합니다.

카드 외곽선은 `#466486` 2px이며 장치 전송용 RGB565 양자화 뒤 일반 카드 배경과 약 3.12:1 대비를 유지합니다. 제목 왼쪽의 4px rail은 CPU cyan, GPU violet, RAM green, AI blue를 사용하고, 가로 레이아웃에서는 제목 아래에 같은 색의 2px 밑줄을 추가합니다. 색은 위치를 빠르게 찾는 보조 단서일 뿐이며 `CPU`, `GPU`, `RAM`, `CODEX LIMITS` 텍스트가 항상 1차 구분 수단입니다.

AI 제목도 18px을 우선하고 상태 배지와의 8px 간격을 지키도록 필요한 경우에만 더 작은 ExtraBold 크기로 맞춘 뒤, 그래도 들어가지 않는 임의의 긴 제목에만 ASCII `...`를 사용합니다. 가로·세로, 경계값, 모델명 유무와 AI 상태를 조합한 독립 렌더 행렬 1,440건에서 텍스트 잘림·텍스트 겹침·수치/게이지/그래프 충돌은 모두 0건이었습니다. 이 수치는 네이티브 PNG와 좌표 검사 결과이며, 실제 3.5인치 LCD를 약 40~50cm 거리에서 보는 최종 가독성 확인은 사용자 실물 QA로 남아 있습니다.

## Codex 로컬 한도 설정

설정 창의 `Codex 로컬 한도 · 추천`은 이 앱이 제공하는 동의 기반 로컬 provider입니다. 체크박스에 명시적으로 동의하기 전에는 세션 경로를 확인하거나 파일을 열지 않습니다. provider 자체는 로그인 화면, OpenAI/ChatGPT API 키, 네트워크 연결을 사용하지 않습니다. 다만 Codex가 이 PC에 로컬 세션과 rate-limit 이벤트를 먼저 기록한 상태여야 값이 나타납니다.

세션 검색 경로는 다음 우선순위입니다.

1. `CODEX_HOME`이 명시되어 있으면 `%CODEX_HOME%\sessions`만 사용합니다. 해당 위치가 없더라도 profile 경로로 몰래 대체하지 않습니다.
2. `CODEX_HOME`이 명시되지 않았을 때만 `%USERPROFILE%\.codex\sessions`를 사용합니다.

다른 PC 형태의 합성 시험에서는 공백과 한글이 포함된 `USERPROFILE`의 기본 `.codex` 경로, 공백이 포함된 custom `CODEX_HOME` 우선순위, 존재하지 않는 명시적 `CODEX_HOME`의 profile fallback 금지를 확인했습니다. 모든 경로와 JSONL은 격리된 임시 폴더로 만들었으며 실제 다른 PC나 다른 사용자의 파일에는 접근하지 않았습니다. 실제 다른 PC 검증은 대기 중입니다.

85%가 100%로 표시된 직접 원인은 일반 Pro 한도와 이름이 붙은 별도 `GPT-5.3-Codex-Spark` 한도를 같은 값으로 취급한 것이었습니다. 현재 로컬 기록에는 일반 7일 한도가 `15% USED`, 즉 `85% LEFT`였지만 Spark 별도 한도는 `0% USED`, 즉 `100% LEFT`였습니다. 이전 parser는 파일 수정 시각 순서와 한도 이름을 충분히 구분하지 않아 Spark 100%를 일반 한도처럼 선택할 수 있었습니다. 수정된 parser는 `limit_name`이 없거나 `null`인 일반 한도만 사용하고, Spark처럼 이름이 붙은 별도 한도는 제외합니다. 파일 수정 시각이 아니라 이벤트 내부 시각을 전체 파일에서 비교하고, 미래 시각 이벤트는 거부하며, 서로 다른 이벤트·파일에 나뉜 5H와 7D 값은 창별 최신값끼리 합칩니다.

AI 카드의 큰 수치는 **7일 남은 사용량**을 우선 표시합니다. 현재 값이 있는 5시간 한도는 보조 `5H LEFT`로만 표시하고, 현재 7일 값이 없을 때만 큰 수치의 대체값으로 사용합니다. 오래된 5시간 값은 현재 7일 값 옆에 섞어 표시하지 않습니다. 예를 들어 `7D USED 15%`이면 큰 값은 `85%`, 라벨은 `7D LEFT`가 되며 게이지는 `7D USED 15%`만큼 찹니다. `5H RESET`과 중복 `UPDATED` 항목은 표시하지 않고, 적용 가능한 7일 reset만 보조 정보로 표시합니다. 일시적으로 더 오래되거나 불완전한 스캔 결과가 도착하면 controller는 이미 가진 더 최신 수치를 유지하되 `STALE`/`DELAYED`를 표시하며, 명시적인 읽기 오류는 숨기지 않습니다. JSONL에서는 엄격히 허용한 `event_msg → token_count → rate_limits` 형태의 숫자·시각·한도 구분 정보만 결과 객체에 유지합니다. 프롬프트, 답변, 도구 출력과 그 밖의 payload는 화면·상태·로그에 보관하거나 반환하지 않으며 네트워크로 전송하지 않습니다.

2026-08-13 최초 문제 재현 시 안전한 로컬 메타데이터의 일반 7일 남은 값은 `85%`였고, 이름 있는 Spark 별도 한도만 `100%`였습니다. 당시 fresh onedir를 만든 뒤 일반 값이 `84%`로 갱신됐으며, source 메타데이터 전용 읽기와 당시 EXE 화면이 `84%`/`7D LEFT`로 정확히 일치했습니다. 해당 packaged live smoke와 [`build/codex-85-packaged-smoke.png`](build/codex-85-packaged-smoke.png)은 화면 전체 캔버스·격자 제거 전의 역사적 기록이며 현재 변경의 package 증거가 아닙니다.

[Codex 환경 변수 공식 안내](https://learn.chatgpt.com/codex/config-file/environment-variables)에 나온 Codex home 경로 규칙(`CODEX_HOME` 또는 기본 `%USERPROFILE%\.codex`)과 내부 세션 JSONL event schema의 안정성은 구분해야 합니다. 경로 규칙은 지원되는 안정적 위치로 취급하지만, 이 사용량 표시는 **OpenAI가 제공하는 공식 한도 API가 아닌 best-effort 로컬 호환 기능**입니다. `event_msg → token_count → rate_limits` 구조는 공식적으로 보장된 공개 계약으로 간주하지 않으며 Codex의 기록 형식, 필드 이름 또는 한도 창 구조가 바뀌면 `NO DATA` 또는 `UNAVAILABLE`이 될 수 있습니다. 따라서 값은 편의용 상태 표시이며 계정 결제·정책·공식 대시보드의 권위 있는 증빙으로 사용하면 안 됩니다.

연구 과정에서는 [bemaru의 TrafficMonitor AI Usage Limits 플러그인](https://github.com/bemaru/trafficmonitor-ai-usage-plugin)의 로컬 세션 경로와 5시간/7일 표시 방식을 참고했습니다. 해당 플러그인의 코드, DLL, helper 또는 배포 바이너리는 이 프로젝트에 포함하지 않았으며, 이 앱의 consent gate·경로 검증·JSON shape 검증·표시 변환은 Python으로 직접 구현했습니다.

## OpenAI Usage/Costs 설정

`OpenAI API 조직 사용량`은 Codex 로컬 한도와 별개의 선택 옵션입니다. 웹 대시보드나 ChatGPT 화면을 스크래핑하지 않고 공식 조직 API인 `GET /v1/organization/usage/completions`와 `GET /v1/organization/costs`만 사용합니다. 표시 가능한 값은 API 요청 수, 입력·출력·캐시 토큰, 오늘·이번 달 API 비용입니다.

- [OpenAI 공식 Usage API 문서](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions)
- [OpenAI 공식 Costs API 문서](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)

개인 ChatGPT Plus/Pro의 메시지 한도나 남은 사용량은 이 API의 값이 아닙니다. 앱은 이를 추정하거나 가짜 할당량으로 표시하지 않습니다.

### Admin Key를 안전하게 저장하기

Admin Key를 이 JSON 설정 파일, 명령행 인수, 환경 변수, 로그, Codex/ChatGPT 대화 또는 메신저에 붙여넣지 마십시오. 배포 폴더에서 다음 명령을 직접 실행합니다.

```powershell
.\AI-Mini-Monitor-CLI.exe --set-openai-key
```

또는 프로젝트 폴더에서:

```powershell
.\scripts\Set-OpenAIKey.ps1
```

키는 콘솔에 보이지 않는 입력으로 두 번 확인한 뒤 현재 Windows 사용자 범위 DPAPI로 암호화됩니다. 암호문 경로는 `%LOCALAPPDATA%\AI-Mini-Monitor\secrets\openai_admin_key.dpapi`입니다. 평문 키는 설정 JSON에 저장하거나 출력하지 않습니다. API 요청 시에만 `api.openai.com`의 위 공식 엔드포인트에 Bearer 자격 증명으로 사용됩니다.

DPAPI 암호문은 같은 Windows 사용자 프로필에서만 복호화할 수 있습니다. 다른 PC나 사용자 계정으로 배포 폴더를 복사할 때는 키 파일을 복사하지 말고 해당 계정에서 다시 설정하십시오.

키 제거:

```powershell
.\AI-Mini-Monitor-CLI.exe --clear-openai-key
```

`--set-openai-key`는 일반 설정의 `ai.provider`를 `openai_api`로 바꿉니다. HTTP 401, 403, 429와 네트워크/응답 오류는 서로 구분하며 마지막 성공 데이터를 캐시할 수 있지만, 오래된 값은 지연 상태로 표시합니다.

## ChatGPT Activity 개인정보 보호 모드

`ChatGPT/Codex 앱 활동 시간`은 Codex 한도나 OpenAI API 조직 Usage/Costs와 별개의 선택 옵션입니다. 조직 Admin Key를 사용하지 않으려면 설정 창에서 이 항목을 선택하거나 `ai.provider`를 `chatgpt_activity`로 바꿀 수 있습니다.

```json
{
  "ai": {
    "provider": "chatgpt_activity",
    "usage_refresh_seconds": 60,
    "cost_refresh_seconds": 600,
    "daily_budget_usd": null,
    "monthly_budget_usd": null,
    "activity_processes": ["ChatGPT.exe", "Codex.exe"]
  }
}
```

이 모드는 현재 포그라운드 창을 소유한 **프로세스 실행 파일 이름만** 확인합니다. 창 제목, 대화 내용, 입력 내용, URL, 화면 이미지, 네트워크 트래픽은 읽거나 저장하지 않습니다. ChatGPT의 실제 사용량이나 메시지 한도를 뜻하지도 않습니다.

로컬 파일 `%LOCALAPPDATA%\AI-Mini-Monitor\activity.json`에는 당일 활성 시간, 세션 수, 마지막 활성 시각만 저장됩니다. 추적 대상 실행 파일 이름은 `activity_processes`에서 명시적으로 제한할 수 있습니다.

## 미리보기와 DEMO 데이터

일반 GUI의 설정/제어 창에는 선택한 framebuffer 미리보기가 항상 내장됩니다. 가로 480×320은 그대로 표시하고 세로 320×480은 고정된 미리보기 영역에 aspect-fit해 왜곡 없이 표시합니다. 시작 전에는 `PRESS MONITOR START`, 시작 후에는 현재 controller의 최신 프레임을 표시하며 이 내장 표시 자체는 COM을 열지 않습니다. 일반 미리보기용 별도 창은 제거됐고, PC 위에 띄울 때는 바로 아래의 상태 인식형 `PC 상태창 켜기`/`PC 상태창 끄기`를 사용합니다.

PC 상시 표시 상태창도 같은 데이터와 대시보드 구성의 프레임을 사용하지만, 일반 미리보기와 달리 제목 표시줄 없이 최상단을 유지하고 저장된 크기·연결부 및 4개 카드 면 불투명도·위치를 적용합니다. 화면 전체 캔버스와 격자는 표시하지 않고, 전경의 글자·숫자·게이지·그래프는 불투명하게 유지합니다. 물리 LCD용 프레임에는 기존 격자가 그대로 남습니다. 일반 미리보기와 PC 상태창을 동시에 표시해도 두 창을 위해 센서나 COM 연결을 추가로 만들지 않습니다.

아래 CLI 미리보기는 실측값이 아닌 합성 데이터이며 화면에 `DEMO`가 명확히 표시됩니다. 이 명령도 직렬 포트를 열거나 실장치에 전송하지 않습니다.

```powershell
# 한 상태를 저장된 방향의 네이티브 PNG로 저장하고 창으로 표시
.\AI-Mini-Monitor-CLI.exe --preview temperature_warning `
  --preview-output .\temperature_warning.png

# 창을 열지 않고 저장만 수행
.\AI-Mini-Monitor-CLI.exe --preview memory_99 `
  --preview-output .\memory_99.png --no-window

# 정식 회귀용 9개 가로 480×320 검증 상태를 모두 생성
.\AI-Mini-Monitor-CLI.exe --render-previews .\previews
```

단일 `--preview`는 저장된 `device.rotation`을 따릅니다. 반면 `--render-previews`의 정식 회귀 세트는 비교 가능한 9개 가로 480×320 자산을 계속 생성합니다. 상태 이름은 `normal`, `zero`, `hundred`, `temperature_warning`, `memory_99`, `ai_not_configured`, `ai_delayed`, `reconnecting`, `disconnected`입니다. 현재 packaged CLI에서 버전·읽기 전용 진단(`read_only: true`, `serial_port_opened: false`, `secret_value_included: false`)과 9개 RGB 480×320 미리보기 생성을 자동 검증했습니다. 어느 결과도 실물 LCD 전송 성공을 뜻하지 않습니다.

## 시작 프로그램과 단일 실행

자동 시작은 설치나 첫 실행 때 등록되지 않습니다. 설정 창의 `Windows 시작 시 자동 실행` 체크박스 하나로 등록과 해제를 전환합니다. 앱은 현재 사용자 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`의 **앱 전용 값 하나만** 변경하고 다른 시작 항목은 건드리지 않습니다. 등록 명령은 정확한 GUI 실행 파일, `--minimized`, 선택한 custom config 경로를 포함하며, 쓰기 후 등록값을 다시 읽어 예상 명령과 정확히 일치할 때만 `켜짐`으로 표시합니다. 다른 명령이 이 앱 값에 들어 있으면 `재설정 필요`로 표시합니다. 쓰기 요청 뒤 확인 읽기만 실패하면 반대 상태로 거짓 롤백하지 않고 `확인 필요`로 남기므로 다시 눌러 재검증할 수 있습니다. 이 `--minimized` 경로는 저장된 설정으로 즉시 Start를 요청하므로, 일반 setup-first 실행과 달리 저장된 동의나 설정을 새로 부여하지 않은 채 모니터 연결을 시작할 수 있습니다.

CLI에서도 같은 앱 전용 값을 명시적으로 변경할 수 있습니다.

```powershell
.\AI-Mini-Monitor-CLI.exe --enable-autostart
```

해제:

```powershell
.\AI-Mini-Monitor-CLI.exe --disable-autostart
```

GUI 체크박스와 CLI는 동일한 명령 생성·readback 검증을 사용합니다. 자동 시험은 가짜 registry 구현을 사용하므로 실제 Windows registry에 쓰지 않습니다. 같은 Windows 세션에서 두 번째 GUI 인스턴스를 실행하면 안내 메시지를 표시하고 종료하며, 기존 프로세스를 건드리지 않습니다.

## 문제 해결

### 설정 창 하단에서 `프로그램 종료`가 잘렸던 문제

1024×720급 작업 영역에서 기존 창의 실제 client 높이는 696px이었지만 왼쪽 콘텐츠가 요구한 높이는 713px이어서 17px이 부족했습니다. 그 결과 39px 높이의 `프로그램 종료` 버튼 중 22px만 보였습니다. 전체 화면 높이에서 고정값을 빼던 기존 맞춤 로직이 Windows 작업표시줄과 제목 표시줄·테두리를 정확히 계산하지 못한 것이 원인입니다.

이 하단 잘림 수정은 현재 소스에 유지되며 작은 화면 제목 변경 전의 기존 배포 EXE에도 포함돼 있습니다. `PerMonitorV2` DPI 인식(`PerMonitor`, `true/pm` fallback)을 사용하고, Win32의 모니터별 `rcWork`와 현재 창의 frame 크기를 계산해 외곽 전체를 사용 가능한 영역 안에 맞춥니다. Win32 모니터 조회가 일부 실패해도 `SPI_GETWORKAREA`와 보수적 frame 여유를 유지합니다. 왼쪽 장치·AI 설정 카드는 독립 scroll viewport에 두고 Start·Stop·Exit와 자동 실행 체크박스는 항상 보이는 고정 푸터에 두었습니다. 키보드 포커스가 화면 밖 설정으로 이동하면 해당 제어가 보이도록 자동 스크롤하고, 휠 입력은 스크롤 영역에 포인터가 있을 때만 소비합니다.

### PC 상태창이 보이지 않거나 잘못된 모니터에 표시됨

1. 설정 창 메인의 `PC 상태창 켜기`를 누르거나 트레이의 `PC 상태창 켜기/끄기`를 전환합니다. 버튼이 `PC 상태창 끄기`라면 현재 표시 상태입니다.
2. `PC 상태창 설정`에서 연결부와 4개 카드 면의 불투명도 및 크기를 확인합니다. 화면 전체 캔버스와 격자는 불투명도와 관계없이 표시되지 않습니다. 카드 불투명도 0%에서도 글자·숫자·게이지·그래프는 alpha 255로 남습니다. 창 전체 드래그를 유지하는 native 1/255 입력 면은 무색이며 격자 RGB를 포함하지 않습니다.
3. 모니터를 분리하거나 배치를 바꾼 경우 저장된 음수 좌표가 더 이상 유효하지 않을 수 있습니다. 앱은 다음 표시 때 사용 가능한 작업 영역으로 위치를 복구합니다.
4. 상태창의 센서 값이 멈춘 것처럼 보이면 `모니터 중지` 상태인지 확인합니다. 중지 중에는 마지막/정적 프레임을 유지하고 `모니터 시작` 뒤 실시간 값이 다시 갱신됩니다.

### `PORT IN USE · COM3` 또는 액세스 거부

2026-08-10 17:29 KST에 관찰한 검은 화면 사례는 정확한 USB 장치 열거 뒤 COM3 open이 `PermissionError(13, AccessDenied)`로 거부된 경우였습니다. 포트 handle을 얻지 못해 전송 바이트는 0이었으며, 당시 배포본의 `RUNNING` 표시는 실제 연결 성공이 아니었습니다.

1. UsbMonitor와 시리얼 터미널 등 COM3을 사용할 수 있는 앱을 창뿐 아니라 알림 영역에서도 완전히 종료합니다.
2. 설정 창에서 `모니터 시작`을 다시 누릅니다. 실행 중 연결이 끊긴 경우에는 `장치 다시 연결`을 누릅니다.
3. 앱은 다른 프로세스를 자동 종료하지 않습니다. 관리자 권한 실행을 우선 해결책으로 요구하지도 않습니다.
4. 계속 실패하면 USB를 다시 연결하고 `장치 찾기`로 VID/PID/일련번호를 확인합니다.

### `DISCONNECTED` 또는 `RECONNECTING`

1. 제조사 프로그램과 다른 시리얼 터미널이 COM 포트를 사용 중이지 않은지 확인합니다.
2. USB 케이블을 점검한 뒤 읽기 전용 진단을 실행합니다.
3. 진단의 `exact_targets`에 현재 COM 포트가 있어야 하며 VID, PID, 일련번호가 모두 정확해야 합니다.
4. GUI의 설정 창이나 트레이 메뉴에서 `장치 다시 연결`을 선택합니다.
5. COM 번호가 바뀌는 환경에서는 `manual_port`를 `null`로 두는 것이 좋습니다.

임의의 COM 포트를 우회 지정하거나 다른 장치에 시험 패킷을 보내지 마십시오. 수동 포트 지정도 안전 식별자 검사를 통과해야 합니다.

### CPU 온도가 `--`

진단에서 CPU 온도 후보의 `value`가 비어 있다면 현재 권한/하드웨어 조합에서 값이 제공되지 않는 것입니다. 존재하지 않는 값을 추정하지 않습니다. GPU 온도 등 다른 센서가 정상인지 확인하고, CPU 온도 확보만을 위해 출처가 불명확한 커널 드라이버를 설치하지 마십시오.

### GPU가 잘못 선택되거나 값이 `--`

진단의 `candidate_sensors`에서 원하는 GPU의 부하/온도 식별자와 실제 값을 확인해 `gpu_load_sensor`, `gpu_temperature_sensor`에 지정합니다. 지정 후 앱을 다시 시작합니다.

### OpenAI 상태가 401/403/429 또는 지연

- 401: 키가 잘못됐거나 폐기됐는지 확인합니다.
- 403: Admin Key의 조직 권한이 Usage/Costs 조회를 허용하는지 확인합니다.
- 429: 호출 제한 상태입니다. 앱은 Usage 최소 60초, Costs 최소 600초 간격을 지킵니다.
- 지연/네트워크: 방화벽, 프록시, 시스템 시간과 `api.openai.com` 연결을 확인합니다.

키를 문제 해결 메시지나 진단 JSON에 추가하지 마십시오. 진단 보고서는 키 존재 여부만 기록하고 키 내용은 포함하지 않습니다.

### 설정 오류로 시작하지 않음

`device.usb_fps`, `app.render_fps`, 센서 간격, API 갱신 간격이 위 허용 범위 안인지 확인합니다. `rotation`은 `landscape`, `landscape_inverted`, `portrait`, `portrait_inverted` 중 하나여야 합니다. 원인을 확인할 때는 `%LOCALAPPDATA%\AI-Mini-Monitor\logs\ai-mini-monitor.log`를 사용하십시오. 로그는 1MB 단위로 최대 3개 백업을 순환하며 알려진 자격 증명 패턴을 마스킹합니다.

### SmartScreen 경고

현재 자체 빌드의 Authenticode 상태는 `NotSigned`이며 게시자 인증이나 Windows 평판을 제공하지 않습니다. 배포 출처를 먼저 확인하고 `SHA256SUMS.txt`와 파일 해시를 대조하십시오. 단, 공격자가 실행 파일과 같은 폴더의 해시 목록을 함께 바꾸면 내부 대조만으로는 이를 식별할 수 있습니다. `SHA256SUMS.txt`는 Authenticode 서명이나 신뢰 사슬의 대체물이 아닙니다. 신뢰할 수 있는 외부 기준 해시를 확보할 수 없다면 실행하지 마십시오.

```powershell
Get-FileHash .\AI-Mini-Monitor.exe -Algorithm SHA256
Get-FileHash .\AI-Mini-Monitor-CLI.exe -Algorithm SHA256
```

## 소스 빌드와 테스트

재현 빌드 스크립트는 현재 로컬에서 사용 가능한 64비트 CPython **3.13.3**과 PyInstaller 6.22.0을 고정 확인합니다. 조사 시 권장한 유지보수 버전은 CPython **3.13.14**였으나 이번 로컬 빌드·시험 환경에는 설치하지 않았습니다. 따라서 3.13.3은 이번 산출물의 확인된 빌드 인터프리터이지 최신 권장 패치 버전이라는 뜻이 아닙니다. 3.13.14로 전환할 때는 빌드 핀과 의존성을 갱신하고 전체 자동 시험, 패키징 smoke, 짧은 실장치 회귀를 다시 수행해야 합니다. 패키지 사용자는 어느 버전의 Python도 별도로 설치할 필요가 없습니다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\Build.ps1
```

빌드 결과는 `dist\AI-Mini-Monitor`에 생성됩니다. `Build.ps1`은 테스트, 런타임 버전, 입력 지문, GUI/CLI PE 서브시스템, 포함 리소스를 검사하고 `BUILD-INFO.json`과 `SHA256SUMS.txt`를 생성합니다.

현재 격자 없는 오버레이를 포함한 onedir와 ordered 전체 검증은 독립 실제 Tk 시험을 포함해 `699 passed, 0 skipped`, 빌드 뒤 패키징·라이선스 검증은 `17 passed, 0 skipped`입니다. packaged CLI `--version`, 읽기 전용 진단, 9개 미리보기와 카드 불투명도 65%·크기 100% no-serial 상태창 smoke도 통과했습니다. artifact는 1,412개 파일이며 manifest 검증을 통과했고, 현재 Win32 캡처는 [`build/overlay-grid-free-packaged-smoke.png`](build/overlay-grid-free-packaged-smoke.png)입니다. 물리 LCD의 네 방향과 실제 밝기 변화, 실제 COM·sleep/resume·USB detach/reconnect, 실제 시작 프로그램 registry 쓰기, 실제 다른 PC 및 혼합 DPI 다중 모니터 시각 검증은 아직 확인하지 않았습니다.

실장치에 데이터를 보내는 벤치마크는 별도의 명시적 확인 스위치가 없으면 거부됩니다. 실행 전 반드시 [DEVICE_BENCHMARK.md](DEVICE_BENCHMARK.md)를 읽으십시오.

## 라이선스와 출처

프로젝트는 `GPL-3.0-or-later`입니다. 제3자 라이브러리와 글꼴은 각자의 라이선스를 유지하며 `THIRD_PARTY_NOTICES.md`, `THIRD_PARTY_COMPONENTS.json`, `third_party` 아래의 원문을 함께 제공합니다.

장치 프로토콜의 제한된 구현은 `turing-smart-screen-python`의 고정 커밋에서 유래했으며, 로컬 구현은 검증된 밝기 `0x6E`, 화면 켜기 `0x6D`, 방향 `0x79`, 비트맵 `0xC5`만 허용합니다. reset, hello, screen-off, clear, firmware 명령은 제공하지 않습니다. 제조사 실행 파일·설정·전용 바이너리는 새 앱에 포함하거나 실행하지 않습니다.
