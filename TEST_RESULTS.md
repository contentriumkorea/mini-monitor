# 시험 결과

이 문서는 2026-08-13 KST 현재 자동 시험, 읽기 전용 시스템 진단, 패키징, 데스크톱 오버레이와 물리 장치 시험을 구분해 기록합니다. 자동 시험 통과는 실제 Windows 혼합 DPI 합성이나 실물 화면 화질 검증을 대신하지 않습니다.

## 요약

| 범주 | 상태 | 근거 |
|---|---|---|
| 현재 격자 없는 overlay source/package ordered 전체 시험 | 통과 | 독립 실제 Tk 포함 699 passed, 0 skipped |
| Codex 사고 재현 메타데이터 | 원인 확인 | 일반 7D 85%와 named Spark 100%가 공존; 일반 한도 대신 Spark를 선택했던 문제 |
| 격자 제거 전 post-build source/package live 일치 | 당시 통과 | 일반 7D 84%; 당시 source와 EXE 모두 84%/7D LEFT, DELAYED |
| 현재 onedir 패키징·라이선스 시험 | 통과 | 빌드 뒤 17 passed, 0 skipped |
| 현재 artifact 무결성 | 통과 | 1,412개 파일; 전체 파일 SHA-256 manifest 검증 |
| PC 상태창 자동 회귀 | 통과 | 상태 인식 메인 토글·설정/트레이 공유·카드 opacity 0~100%·semantic 격자 alpha 0·불투명 foreground·무색 native 입력 면 포함 |
| 현재 격자 없는 overlay packaged 검증 | 통과 | opacity 65%·scale 100%·minimized·no-serial, exit 0·leftover 0; Win32 캡처 확인 |
| 격자 제거 전 packaged 상태창 live smoke | 당시 통과 | no-serial·minimized·overlay, exit 0·leftover 0; 84%/7D LEFT Win32 캡처, 현재 변경의 증거 아님 |
| 직전 fresh packaged 상태창 smoke | 당시 통과 | opacity 0%·scale 75%·minimized·no-serial, exit 0·leftover 0; 이번 Codex 수정 전 Win32 캡처 |
| 이전 source/Win32 상태창 smoke | 당시 통과 | 단일 HWND·`ULW_ALPHA`, 캡처와 200회 GDI delta 0; 새 UI 전 역사적 증거 |
| 이전 652-test packaged 상태창 smoke | 당시 통과 | opacity 35%·scale 75%·no-serial exit 0; 현재 기능의 package 증거 아님 |
| 작은 화면 방향·타이포그래피 렌더 | 통과 | 독립 1,440개 일반 행렬과 CODEX 실제 상태 684개 행렬에서 clip·overlap·collision 0 |
| 읽기 전용 실제 센서/장치 진단 | 통과 | COM3 정확 일치, 직렬 포트 미개방, 키 미포함 |
| 17:29 사용자 실행 | 실패 원인 확인 | COM3 AccessDenied, open 실패, 전송 0 bytes; 당시 RUNNING 표시는 오판 |
| 방향 기능 전 실제 COM3 headless smoke | 당시 통과 | 업데이트 9/9, 영역 47/47, 전체 프레임 1/1, 오류 0, clean stop; 현재 네 방향 증거 아님 |
| 변경 전 실장치 프로토콜 단기 교정 | 당시 통과 | 교정·부분 측정 업데이트 29/29 완료, 오류 0, 최대 대기 1 |
| 변경 전 실제 컨트롤러 60초 실행 | 당시 통과 | 직렬/worker 오류 0, 최대 대기 1, 메모리 증가 의심 없음 |
| 변경 전 setup-first 스냅샷 30초 실장치 회귀 | 당시 통과 | 30.000204초, 오류 0, 최대 대기 1, 정상 종료; 현재 연결 증거 아님 |
| 격자 제거 전 packaged CLI·진단·미리보기 | 당시 통과 | version·read-only diagnose·RGB 480×320 미리보기 9개 자동 확인 |
| 실제 시작 프로그램 registry 쓰기 | 대기 | 자동 시험은 가짜 registry만 사용 |
| 변경 전 setup-first 스냅샷 30분 실장치 내구성 | 미완료 | 사용자 요청으로 중지, 완료 JSON 없음 |
| 사용자 실물 사진/영상 시각 검증 | 대기 | 자동화로 대체 불가 |
| 실제 sleep/detach 복귀 검증 | 대기 | 시험 중 실제 절전·케이블 분리 이벤트 없음 |

## 자동 시험

실행 명령:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

현재 격자 없는 오버레이 source/package ordered 전체 자동 검증 결과:

```text
699 passed, 0 skipped
```

현재 source/package ordered 전체 시험은 `699 passed, 0 skipped`, 빌드 뒤 패키징·라이선스 시험은 `17 passed, 0 skipped`입니다. 최초 사고 재현 때 일반 한도는 85%, named Spark 별도 한도는 100%였고, named 한도를 일반 값으로 잘못 표시하던 문제를 수정했습니다. 현재 fresh package·manifest·packaged CLI·Win32 상태창 smoke까지 확인했습니다.

시험 범위:

- 가로 480×320과 세로 320×480 좌표, 카드 면적 비율, 경계와 클리핑
- 가로·세로의 0%, 9%, 10%, 78%, 99%, 100%, `--`, 온도 경고, 연결/AI 오류 상태 렌더링과 PNG 육안 검토
- 대각선 3.5인치·480×320의 약 164.8ppi, 1px 약 0.154mm 기준에서 기존 11px Inter SemiBold 회색 제목과 새 18px Inter ExtraBold 흰색 제목 비교
- `#466486` 2px 외곽선의 RGB565 양자화 후 일반 카드 대비 약 3.12:1, CPU cyan·GPU violet·RAM green·AI blue 4px rail, 가로 화면 2px 밑줄의 픽셀·색상·카드 내부 배치
- 색은 보조 단서로만 사용하고 `CPU`, `GPU`, `RAM`, `CODEX LIMITS` 텍스트를 1차 식별자로 유지하며, AI 제목은 18px을 우선하고 상태 배지와 8px 이상 간격 유지
- 가로·세로, 경계값, 모델명 유무와 AI 상태를 조합한 독립 1,440개 행렬에서 텍스트 클리핑 0건, 실제 그려진 텍스트 겹침 0건, 수치/게이지/그래프 충돌 0건
- CODEX quota `0%`·`9%`·`10%`·`78%`·`99%`·`100%`·`--`가 RAM과 같은 상대 bbox·오른쪽 anchor·글꼴을 사용하는지 확인. 가로 44px `(112, 83)`, 세로 40px `(112, 69)`, `%` 절반 크기와 동일한 하단 기준선이며 실제 상태 렌더 684건에서 clip·수치/라벨/바 충돌 0건
- 시간 기반 EMA와 위험값 빠른 반응, 원시/표시 값 분리
- 활성 가로·세로 크기에 맞는 위젯 단위 dirty rectangle, 세로 하단 갱신 보존 및 과도한 병합 방지
- Rev-A 밝기 `0x6E`/SCREEN_ON `0x6D`/방향 `0x79`/비트맵 `0xC5` 명령 golden bytes, exact allowlist, RGB565LE 변환, 범위·길이 검증
- 화면 밝기 1%·50% 경계와 기본 25%, 정수가 아닌 값·0%·51% 거부. 설정 창 slider 120ms debounce와 latest-value coalescing, 정지 중 저장 전용, 실행 중 기존 단일 writer live apply, 실패 rollback 검증
- 초기 Start·재연결·절전 복귀에 최신 밝기를 다시 보내며 brightness 변경이 reset·hello·screen-off, 두 번째 writer 또는 두 번째 COM handle을 만들지 않는지 fake serial로 확인
- VID/PID/일련번호 모두 일치하는 장치만 선택하고 수동 COM 우회 차단
- 단일 serial writer, 최신값 우선 최대 1건, 재연결·절전 복귀 뒤 선택한 방향과 활성 크기 전체 프레임 복원
- 네 canonical 설정 `landscape`, `landscape_inverted`, `portrait`, `portrait_inverted`의 장치 방향 값과 480×320/320×480 활성 크기
- 115200 8-N-1, Handshake.None, DTR/RTS ON과 저장된 밝기 1~50%(기본 25%) → SCREEN_ON → 100ms → 선택한 방향 → 500ms → 최신 밝기 재확인 → 활성 크기 첫 전체 프레임 → ONLINE 순서 및 제한 시간 내 Start 성공 판정
- COM AccessDenied를 `PORT IN USE`로 분류하고 명시적 재시작 전 반복 open·중복 경고를 멈추며 raw 오류를 UI에 노출하지 않는지 확인
- controller 첫 전체 프레임 뒤 부분 업데이트, 제출/완료 통계
- OpenAI 공식 Usage/Costs 응답 파싱, 페이지네이션, 캐시와 401/403/429/네트워크 오류
- Windows 사용자 범위 DPAPI 왕복과 암호문에 평문이 남지 않는지 확인
- 로그/오류 메시지 자격 증명 마스킹
- ChatGPT Activity가 포그라운드 프로세스 이름만 사용하고 로컬 일별 통계를 저장하는지 확인
- CPU/GPU 센서 선택, 값 없음 사유, RAM 수집, CPU/GPU 모델명 전달
- CPU 모델명을 Windows registry에서 읽기 전용으로 한 번만 조회하고 안전한 fallback을 사용하는지, GPU 모델명이 선택된 non-virtual LibreHardwareMonitor 센서와 동일한 물리 GPU인지 확인. 모델명 제어 문자 제거·공백 정규화·64자 제한·화면 `...` 처리와 수치/게이지/그래프 무재배치 검증
- 현재 사용자 시작 프로그램 등록/해제와 세션 단일 인스턴스
- Windows suspend/resume 이벤트에서 직렬 소유자 일시 중지·종료와 엄격한 재탐지·전체 프레임 복원
- setup-first 창, 비동기 Start/Stop, 종료 경쟁 조건, COM open admission과 bounded stop/suspend/resume
- 동의 전 파일 접근이 없는 Codex 로컬 5H/7D parser, bounded scan, stale 상태와 경로·내용 비노출
- 격리 합성 경로에서 공백·한글 `USERPROFILE`의 기본 `%USERPROFILE%\.codex\sessions`, custom `CODEX_HOME\sessions` 우선순위, 명시했지만 존재하지 않는 `CODEX_HOME`의 profile fallback 금지. 실제 다른 PC에는 접근하지 않음
- 공식 Codex home 경로 규칙과 best-effort 내부 JSONL `event_msg → token_count → rate_limits` schema의 안정성을 별개로 취급
- 이름 없는 일반 Codex 한도와 `GPT-5.3-Codex-Spark` 같은 이름 있는 별도 한도를 분리. 일반 7D `15% USED`와 Spark `0% USED`가 함께 있을 때 큰 값 `85%`/`7D LEFT`를 유지하고 100%를 선택하지 않는지 확인
- 파일 mtime이 아니라 이벤트 내부 timestamp로 전체 후보를 비교하고, 미래 이벤트를 거부하며, 서로 다른 이벤트·파일의 부분 5H/7D 값을 창별 최신 timestamp로 합치는지 확인
- Codex 큰 값의 7D remaining 우선, 현재 5H remaining 보조·7D 부재 시 fallback, 현재 7D와 stale 5H 혼합 금지, `5H RESET`·`UPDATED` 제거와 큰 값과 같은 창의 USED 극성 게이지
- controller가 이전 fresh 숫자보다 오래되거나 불완전한 transient 결과를 받으면 숫자를 유지하면서 `STALE`/`DELAYED`를 표시하고, 명시적 오류는 그대로 노출하는지 확인
- CPU/GPU/RAM 0~100% 게이지의 0/9/10/99/100% 및 `NO DATA` 경계. CPU/GPU 트랙 가로 206×9px·세로 282×9px, RAM 트랙 가로 206×12px·세로 282×12px, AI 바 가로 206×10px·세로 282×9px 고정
- CPU/GPU/RAM/AI 큰 수치의 JetBrains Mono ExtraBold 적용과 tabular 정렬. CPU/GPU 값 가로 42px·세로 38px, RAM 값 가로 44px·세로 40px, 일반 AI 퍼센트 가로 40px·세로 34px 확인. CODEX quota는 RAM과 같은 가로 44px·세로 40px 사용
- 실시간 sparkline이 대체로 카드 면적의 약 8~10%만 차지하고 작은 시간 표시는 제거됐는지 확인
- 순수 퍼센트 값의 ExtraBold 숫자 + 오른쪽 아래에 보이는 하단선을 맞춘 약 절반 크기·렌더 높이 `%` 분리 렌더링, `--`·비용·기간·`READY` 등의 전체 크기 단일 run 유지
- 설정 창의 `화면 보기: 가로/세로`, `상하 반전 (180°)`, 실행 중 잠금과 세로 내장 미리보기 aspect-fit. 별도 일반 미리보기 창과 관련 제어가 없는지 확인
- 설정창 메인 Primary 버튼이 마지막 확정 상태에 따라 정확히 `PC 상태창 켜기`/`PC 상태창 끄기`로 바뀌고 즉시 실제 창을 표시/숨김. 실패 시 가능한 경우 실제 창과 저장값을 이전 상태로 원복하고, 불가능하면 관찰된 실제 상태에 버튼과 설정을 동기화하며, 고정 feedback lane 때문에 아래 제어가 이동하지 않는지 확인
- 별도 `PC 상태창 설정`과 트레이 `PC 상태창 켜기/끄기`가 같은 표시 상태를 공유하고, 미니 모니터와 같은 데이터·화면 구성·비율을 사용하는지 확인
- 제목 표시줄 없는 topmost 창, 연결부와 4개 카드 면 opacity 0~100%, scale 50~200% 경계와 가로 480×320·세로 320×480 비율 유지, 잘못된 설정값 거부 확인. 화면 전체 캔버스·격자는 semantic alpha 0이고 글자·숫자·게이지·그래프 내부 foreground alpha는 255 유지
- 상태창 마우스 드래그와 포커스 상태의 `Alt`+방향키 이동, 저장 좌표 roundtrip, 왼쪽/위쪽 보조 모니터 음수 좌표, 작업표시줄 제외 clamp와 모니터 제거·배치 변경 뒤 유효 화면 복구 확인
- 숨김 중 프레임별 resize/PhotoImage 갱신을 생략하고, Stop 뒤 마지막/정적 설정 프레임을 유지하며 Start 뒤 실시간 frame 갱신을 재개하는지 확인
- PC 상태창 표시·이동·설정 경로가 COM open/write, 자동 실행 registry 변경, 전역 keyboard/mouse hook, 별도 serial writer를 만들지 않는지 확인
- 단일 실제 Win32 HWND에 `UpdateLayeredWindow`와 `ULW_ALPHA`를 사용하고 연결부와 4개 카드 면만 0~100% alpha를 가지며 화면 전체 캔버스·격자는 semantic alpha 0, 글자·숫자·게이지·그래프 내부는 alpha 255, 가장자리는 자연스러운 anti-alias alpha를 유지하는지 확인. 전체 사각형 hit-test/drag는 격자 RGB가 없는 무색 native 1/255 입력 면으로 유지
- 오버레이 레이어 생성 뒤 물리 RGB framebuffer 해시가 가로 `a88743bb68c6cad961c3f0e0f17a80a040516e842c545d850b6786394c448f55`, 세로 `05dcd9a44d50dbc2fd69c2a357f45a8aba9b0b689f42e8af07be902c0a32e091`로 그대로 유지되는지 확인
- 실제 오버레이 갱신 200회 전후 GDI object delta 0, 100% 크기 평균 1.377ms/frame과 200% 크기 평균 17.661ms/frame 확인
- `사용량 확인` 비동기 상태 영역의 고정 높이·전체 폭 2줄 요약과 100%·125%·150% 배율에서 창 크기·버튼 위치 유지
- 1024×720급 작업 영역에서 기존 client 696px 대비 콘텐츠 요구 높이 713px으로 17px이 부족하고, 39px 종료 버튼 중 22px만 보이던 하단 잘림 회귀
- EXE의 `PerMonitorV2` DPI 인식(`PerMonitor`, `true/pm` fallback), Win32 모니터별 `rcWork`·frame 계산과 `SPI_GETWORKAREA` fallback으로 창 외곽이 작업표시줄을 침범하지 않고, 왼쪽 설정 카드 scroll viewport와 Start·Stop·Exit·자동 실행 fixed footer가 100%·125%·150% 배율에서 조상 클리핑 없이 유지되는지 확인
- 숨은 설정 제어로 키보드 포커스가 이동할 때 자동으로 보이게 스크롤하고, Windows 마우스 휠을 왼쪽 viewport에서만 처리해 다른 영역의 스크롤을 방해하지 않는지 확인
- Codex 동의와 반전 제어가 네이티브 `ttk.Checkbutton` 포커스·Space 키·disabled 의미를 유지하면서 clam 테마의 `X` 대신 빈 상자/cyan `✓`를 표시하는지 100%·125%·150% 배율에서 확인
- 하나의 자동 실행 `ttk.Checkbutton`이 enable/disable을 모두 처리하고, 쓰기 실패는 확정 상태로 복귀하며 쓰기 후 readback만 실패하면 거짓 상태 대신 `확인 필요`로 남기는지 확인
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`의 앱 전용 값만 변경하며, 정확한 GUI 실행 파일·`--minimized`·custom config 명령 생성과 쓰기 후 readback 일치를 가짜 registry로 검증. 자동 시험은 실제 registry에 쓰지 않음
- 라이선스·고정 출처·제3자 구성품 매니페스트
- PyInstaller onedir, GUI/CLI 분리, no-UPX, 리소스 포함 규칙
- 화면에 금지된 디스크·네트워크·날씨·볼륨·MSI 항목이 없는지 검사

## 미리보기 렌더링

정식 `--render-previews` 회귀 세트는 이전과 동일하게 9개 가로 `480×320` 합성 자산을 만들고 `previews/manifest.json`에 `demo_data: true`를 기록합니다. 현재 packaged CLI의 버전·읽기 전용 진단과 RGB 480×320 미리보기 9개 생성을 자동 확인했습니다. 이전 652-test 패키지의 35%·75% packaged smoke는 역사적 증거입니다. 방향 자체는 자동 시험과 fake serial로 검증합니다.

검증한 9개 파일:

1. `normal.png`
2. `zero.png`
3. `hundred.png`
4. `temperature_warning.png`
5. `memory_99.png`
6. `ai_not_configured.png`
7. `ai_delayed.png`
8. `reconnecting.png`
9. `disconnected.png`

모든 합성 미리보기에는 `DEMO` 표기가 있으며 직렬 포트로 전송되지 않습니다. source canonical 가로 세트의 크기·좌표 경계·텍스트 클리핑 검사는 통과했습니다. 내장 세로 미리보기와 PC 상태창은 같은 데이터·대시보드 구성·내용을 비율대로 사용하지만 서로 다른 창 수명주기와 투명도 합성을 가집니다. 별도 일반 미리보기 창은 제거했습니다. 상태창 자동 시험은 물리 패널이나 실제 혼합 DPI Windows 합성 검증과 별개입니다.

현재 packaged Win32 캡처 [`build/overlay-grid-free-packaged-smoke.png`](build/overlay-grid-free-packaged-smoke.png)은 카드 불투명도 65%·크기 100%에서 화면 전체 캔버스·격자 미표시, 반투명 카드 면, 선명한 전경과 no-serial 종료 코드 0·잔여 프로세스 0을 확인했습니다. `codex-85-packaged-smoke.png`, `final-status-toggle-zero`와 더 이전 캡처는 격자 제거 전 역사적 기록입니다. 실제 혼합 DPI 최종 시각 승인은 대기 중입니다.

## 읽기 전용 실제 환경 진단

결과 파일: `diagnostics/current.json`

- `read_only`: `true`
- `serial_port_opened`: `false`
- 정확히 일치한 장치: COM3, VID `1A86`, PID `5722`, serial `USB35INCHIPSV2`
- 실행 권한: 비관리자
- CPU 사용률: 8.2%
- CPU 온도: 값 없음. 저수준 접근 없이 센서 이름만 보고됨
- GPU 사용률: 2.0%
- GPU 온도: 약 42.02°C
- 시스템 RAM: 20.5%, 약 26.22/127.78GiB 사용
- 수집된 후보 센서: 107개
- 진단 JSON에 키 내용 포함: `false`

이 진단에서는 직렬 포트를 열지 않았고 드라이버 설치, 레지스트리 변경, 관리자 승격을 수행하지 않았습니다.

## 보안·개인정보 시험

- OpenAI Admin Key는 숨김 입력으로 두 번 확인하고 `%LOCALAPPDATA%\AI-Mini-Monitor\secrets\openai_admin_key.dpapi`에 현재 사용자 범위 DPAPI 암호문으로만 저장합니다.
- 키는 일반 설정 JSON, 명령행, 환경 변수, 진단 결과, 로그에 저장하도록 설계하지 않았습니다.
- 실제 Windows DPAPI 왕복 시험에서 암호문에 평문 키가 포함되지 않았습니다.
- 로그 formatter와 filter가 Bearer 토큰, OpenAI 스타일 키와 일반 민감값 패턴을 마스킹하는지 시험했습니다.
- OpenAI 연결은 공식 조직 Usage/Costs API만 사용합니다. 브라우저 스크래핑이나 ChatGPT 개인 할당량 추정 경로가 없습니다.
- ChatGPT Activity는 포그라운드 프로세스 실행 파일 이름만 읽으며 창 제목/콘텐츠를 읽는 API를 사용하지 않습니다.
- 프로토콜 모듈은 검증된 밝기 `0x6E`, 화면 켜기 `0x6D`, 방향 `0x79`, 비트맵 `0xC5`만 생성할 수 있고, 일반 임의 명령 API를 제공하지 않습니다. reset, hello, screen-off, clear, firmware 명령은 없습니다.

## 원본 제조사 폴더 보호

개발 시작 시 `35inchENG`를 읽기 전용으로 확인해 아래 기준선을 만들었습니다.

- 파일: 97개
- 전체 크기: 43,825,426 bytes
- 정규 매니페스트 SHA-256: `67cdaed7898055fd2fcdebd3559b4692756802a870402326d7a45e3f88e12562`
- `UsbMonitor.exe` SHA-256: `e2b6ebf6425f397e57fd1eb7beab11888b6c5b6fe0b3bdb3032cefba7ba4b2c0`
- 제조사 EXE 서명: Valid, 버전 2.2.1.0

`scripts/Verify-Original.ps1`은 원본에 쓰지 않고 현재 매니페스트를 같은 방식으로 다시 계산합니다. 제조사 `.data` 파일을 BinaryFormatter 등으로 역직렬화하지 않았고 제조사 바이너리를 새 배포본에 포함하지 않았습니다.

2026-08-13 최종 배포 감사에서는 현재 폴더가 98개 파일, 43,834,297 bytes, 정규 매니페스트 SHA-256 `657b136fd58120a94f1afdab60899186f26932f84857dac613f419fdd266e340`로 계산되어 위 기준선과 일치하지 않았습니다. 추가된 `logs/20260811_log.txt`(8,871 bytes)는 2026-08-11 제조사 앱의 `Application Start`와 `StartMonitor called` 기록을 포함합니다. 이 파일은 삭제하거나 이동하지 않고 그대로 보존했으며, 현재 AI Mini Monitor 배포본에는 포함되지 않습니다. 따라서 현재 상태에서 `Verify-Original.ps1`은 의도대로 실패하며, 위 97개 기준선은 현재 폴더의 무변경 증거로 사용하지 않습니다.

정적 호출 관계에서는 제조사 Run이 밝기 raw 0(최대) → SCREEN_ON → 약 100ms 뒤 렌더를 시작하고, 앱 종료가 밝기 raw 255(꺼짐)와 종료용 명령을 반복한 뒤 serial을 닫는 흐름을 확인했습니다. 이는 이전 앱이 밝기와 SCREEN_ON을 생략했을 때 제조사 종료 뒤 검은 상태를 해제하지 못할 수 있다는 근거입니다. 현재 앱은 안전상 검증된 1~50%만 허용하고 기본값을 25%로 두며, 제조사의 종료용 command 108/103이나 reset/hello/screen-off/clear/firmware를 허용하지 않습니다.

## 현재 패키징 상태

패키징 규격:

- 64비트 CPython 3.13.3
- PyInstaller 6.22.0
- onedir, UPX 미사용
- `AI-Mini-Monitor.exe`: Windows GUI 서브시스템
- `AI-Mini-Monitor-CLI.exe`: Windows console 서브시스템
- 글꼴, LibreHardwareMonitor 라이브러리·고지, 설정 예시, 라이선스, 잠금 파일 포함
- `BUILD-INFO.json` 입력 지문과 `SHA256SUMS.txt` 생성

현재 격자 없는 오버레이를 포함한 onedir와 ordered 전체 시험은 `699 passed, 0 skipped`, 빌드 뒤 패키징·라이선스 시험은 `17 passed, 0 skipped`입니다. artifact는 1,412개 파일이며 manifest 검증을 통과했습니다.

- 현재 packaged CLI `--version`: `0.1.0`, PASS
- 현재 packaged CLI `--diagnose`: `read_only: true`, `serial_port_opened: false`, `secret_value_included: false`
- 현재 packaged CLI `--render-previews`: canonical 9개 manifest 모두 정확히 RGB 480×320
- 현재 packaged GUI 상태창 smoke: no-serial·최소화, 카드 불투명도 65%·크기 100%, 종료 코드 0·잔여 프로세스 0
- 현재 packaged 캡처: `build/overlay-grid-free-packaged-smoke.png`; 전체 캔버스·격자 미표시, 반투명 카드 면, 선명한 전경 확인
- 격자 제거 전 packaged 캡처 `build/codex-85-packaged-smoke.png`는 역사적 기록
- 이번 수정 전 불투명도 0% 캡처: `build/final-status-toggle-zero/packaged-overlay-zero-clean.png`; 역사적 증거
- 이전 652-test packaged GUI의 불투명도 35%·크기 75% smoke와 `build/qa-packaged-overlay-final/packaged-overlay.png`는 새 UI 전 역사적 증거

현재 격자 없는 오버레이를 포함한 fresh package·manifest·CLI·Win32 상태창 smoke를 확인했습니다. 이 결과는 물리 COM이나 LCD 또는 실제 자동 실행 registry 검증을 대신하지 않습니다.

자체 빌드 EXE의 Authenticode 상태는 `NotSigned`입니다. 최종 폴더의 새 해시 매니페스트를 함께 확인하고 신뢰 가능한 배포 경로에서 폴더 전체를 전달해야 합니다. `SHA256SUMS.txt` 자체는 게시자 신원 인증을 대신하지 않습니다.

## 실장치 결과

단기 수치는 [DEVICE_BENCHMARK.md](DEVICE_BENCHMARK.md)에 자세히 기록했습니다.

- 방향 기능 추가 전 연결·초기화 소스 COM3 smoke: **당시 host write 통과**, uptime 5.673404초, 렌더 36, 업데이트 9/9, 영역 47/47, 전체 프레임 1/1, 최대 대기 1, 직렬·worker 오류 0, 잔여 프로세스 0. 현재 네 방향·320×480 기능의 실장치 증거가 아니며 사용자 물리 LCD 관찰은 대기
- 변경 전 프로토콜 교정: 전체 프레임 약 1968.87ms, 32×32/80×40/160×40/228×89 패치 약 13.15/40.79/81.40/258.52ms
- 변경 전 5초 부분 패치: 24/24 완료, 4.651Hz, 직렬 오류 0, 최대 대기 1
- 변경 전 60초 실제 controller: 직렬 오류 0, worker 오류 0, 최대 대기 1, 메모리 증가 의심 없음
- 변경 전 조정 후 15초: 큐 지연 평균/p95 147.95/242.63ms, 센서→전송 평균/p95 522.70/786.98ms, 오류 0
- 연결·초기화 수정 전 setup-first 스냅샷 30초: **당시 통과(PASS)**, 30.000204초, 완료 업데이트/영역 117/664, 오류 0, 정상 종료. 현재 소스의 물리 증거로 사용하지 않음
- 연결·초기화 수정 전 setup-first 스냅샷 30분 시도: **미완료(NOT COMPLETED)**. 사용자 요청으로 중지했고 완료 결과 JSON이 없으므로 PASS로 기록하지 않음
- 더 이전 스냅샷 30분: 1800.000543초 PASS. 현재 소스의 연결·초기화 또는 장기 증거로 소급하지 않음

## 남은 승인 조건

1. 사용자가 실물 패널 사진/영상을 제공해 가로·가로 반전·세로·세로 반전의 방향과 클리핑, 1%·25%·50% 실제 밝기 변화, 약 40~50cm 거리에서 `CPU`·`GPU`·`RAM`·`CODEX LIMITS` 제목, CPU/GPU 모델명 및 굵은 큰 수치·게이지의 가독성, 색상, 잔상·깜빡임을 확인합니다.
2. 실제 Windows sleep/resume과 USB detach/reconnect 뒤 엄격한 재식별·전체 프레임 복원을 확인합니다.
3. 현재 사용자 계정에서 `Windows 시작 시 자동 실행`을 실제 등록·readback·해제해 앱 전용 registry 값만 바뀌는지 확인합니다.
4. 공백·한글 `USERPROFILE`, custom 및 missing `CODEX_HOME`은 합성 임시 경로가 아닌 실제 다른 PC에서도 같은 규칙으로 동작하는지 확인합니다.
5. 100%·125%·150% 배율이 섞인 실제 다중 모니터에서 PC 상태창의 topmost, 전체 사각형 드래그·`Alt`+방향키, 음수 좌표, 작업표시줄 clamp, 모니터 제거 뒤 복구, 50%·100%·200% 크기와 0%·85%·100% 연결부 및 4개 카드 면 불투명도를 시각 확인합니다. 모든 단계에서 화면 전체 캔버스·격자는 보이지 않아야 하고, 0%에서도 무색 입력 면으로 drag가 되어야 하며 전경은 선명해야 합니다.

현재 격자 없는 오버레이를 포함해 source/package ordered `699 passed, 0 skipped`, 패키징·라이선스 `17 passed, 0 skipped`, CLI 버전·진단·미리보기와 packaged 상태창 smoke 및 manifest를 확인했습니다. 실제 COM 시험, 물리 LCD 표시·밝기 변화, 실제 시작 프로그램 registry 쓰기, 실제 다른 PC 접근과 혼합 DPI 상태창 시각 검증은 수행하지 않았습니다. 사용자 실물 확인, 1~5번과 새 1800초 시험이 끝나기 전에는 물리 화면·실제 복귀·실제 자동 실행 등록·다른 PC 호환성·혼합 DPI 상태창·장시간 내구성 기준을 최종 완료로 표기하지 않습니다.
