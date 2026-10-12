# Native Taskbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 작업표시줄 바의 고해상도 배율·배치·전체화면 문제를 해결하고 안정 버전 0.2.10을 배포한다.

**Architecture:** 바만 Tk Toplevel에서 자체 Win32 HWND로 전환한다. 기존 Tk UI 스레드와 이벤트 루프에서 창 수명을 관리하고, 기존 layered presenter와 읽기 전용 shell 조회를 재사용한다. 사용자 ON 상태와 일시적인 네이티브 숨김 상태를 분리한다.

**Tech Stack:** Python 3.13, ctypes/Win32, Tkinter, Pillow, pytest, PyInstaller, GitHub Releases.

**Spec:** `docs/superpowers/specs/2026-10-12-native-taskbar-design.md` — 사용자가 2026-10-12 승인한 설계.

## Global Constraints

- 작업표시줄 바만 Windows 네이티브 창으로 전환한다. 설정창, PC 오버레이, USB 미니 모니터, 계정 로그인과 사용량 수집 방식은 유지한다.
- 기존 사용자 설정·앱 전용 로그인·자동 실행 상태를 보존한다. 제조사 원본 `35inchENG`는 변경하거나 실행하지 않는다.
- 다른 Codex 앱의 로컬 사용량·인증 자료를 읽지 않는다. 별도의 GUI 스레드나 전역 마우스 훅은 추가하지 않는다.
- 실행 파일의 기존 PerMonitorV2 DPI 선언을 유지한다. 바·알림 영역·모니터 경계·그림 크기는 같은 화면 픽셀 기준으로 처리한다.
- 새 안정 버전 0.2.10으로 서명된 `Mini-Monitor.zip`, `update-v2-manifest.json`, `update-v2-manifest.sig`만 GitHub에 배포한다.
- 구형 업데이터 보호와 원자적 교체·복구·구버전 자동 정리 기능은 유지한다. 이번 요청에 없는 이 PC의 설치·자동 실행 설정 변경은 별도로 수행하지 않는다.
- 모든 로컬 편집은 apply_patch로 수행하고 현재 미배포 변경을 보존한다. 아래 명령은 저장소 루트, Python은 `.venv/Scripts/python.exe` 기준이다.

## Review Focus

1. Win64 포인터 크기, WNDPROC 참조와 생성 중 실패: 핸들 손상 없이 실패/폐기하고 나머지 앱은 계속 동작한다 — Task 1.
2. 드래그 중 캡처 취소·폐기·콜백 예외: 포커스를 가져오지 않으며 캡처와 드래그 상태를 정리한다 — Task 1/3.
3. 같은 수치에서 DPI/주 모니터 변경: 다시 그리며 음수 좌표에서도 상대 가로 위치를 유지한다 — Task 2/3.
4. 숨김 중 크레딧 확장·항목 변경·알림 영역 축소: 최신 데이터로 복귀하며 공간 부족이면 화면 위로 떠오르지 않는다 — Task 3.
5. 업데이트의 새 실행 실패 또는 잘못된 공개 파일: 구버전 복구, 설정 보존, 서명/해시 거부를 확인하고 배포 완료로 오인하지 않는다 — Task 4.

---

### Task 1: 독립 네이티브 창과 입력 수명

**Files:** Create `src/ai_mini_monitor/ui/native_taskbar.py`, `tests/test_native_taskbar.py`; Modify `src/ai_mini_monitor/ui/layered_window.py`의 미배포 `no_activate` 실험 부분만 제거.

**Interfaces:** `NativeTaskbarSurface(*, on_drag: Callable[[int], None])`; `hwnd: int`, `winfo_id() -> int`, `show() -> None`, `hide() -> None`, `destroy() -> None`. `on_drag(delta_x)`는 마우스 이동의 화면 픽셀 차이이며 세로 이동은 전달하지 않는다. 기존 `LayeredWindowPresenter(window).present(image, *, x, y)`에 surface를 전달한다. 테스트는 모듈의 WinDLL 경계만 fake로 교체한다.

- [ ] **Step 1:** `tests/test_native_taskbar.py`에 `test_surface_is_stable_and_never_activates`, `test_partial_creation_failure_releases_resources`, `test_drag_capture_cancel_and_destroy`, `test_callback_exception_does_not_escape_wndproc`를 먼저 작성한다. 반복 show/hide에서 HWND 동일, `GetForegroundWindow` 동일, `WM_MOUSEACTIVATE` 결과 `MA_NOACTIVATE=3`, 종료 두 번 안전, 실패 후 살아 있는 HWND 없음, 취소 후 이동 콜백 없음, 콜백 예외가 Win32 경계 밖으로 전파되지 않음을 각각 assert한다. 실제 창 테스트는 격리 subprocess에서 실행하고 자체 창만 폐기한다.
- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_native_taskbar.py -q` → 새 모듈 부재로 FAIL 확인.
- [ ] **Step 3:** 위 interface를 구현한다. `WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`, `WS_POPUP`, `SW_SHOWNOACTIVATE=4`를 사용한다. HWND/WPARAM/LPARAM/LRESULT는 포인터 크기 자료형, 클래스 등록/해제와 WNDPROC 강한 참조는 전체 창 수명에 걸쳐 유지한다. 생성 실패 정리, `WM_LBUTTONDOWN/MOUSEMOVE/LBUTTONUP/CAPTURECHANGED/CANCELMODE/NCDESTROY` 수명 처리를 추가한다. 예외는 안전하게 기록하되 비밀/계정 데이터를 로그에 넣지 않는다. `PostQuitMessage`로 Tk 앱을 종료하지 않는다.
- [ ] **Step 4:** 미배포 Tk 전용 `no_activate`, `set_visible_without_activation`, ShowWindow API 추가를 원래 형태로 되돌린다. 네이티브 surface가 이미 NOACTIVATE 속성을 소유하며 기존 presenter는 그대로 유지한다.
- [ ] **Step 5:** `.venv/Scripts/python.exe -m pytest tests/test_native_taskbar.py tests/test_layered_window.py tests/test_overlay.py -q` → PASS. show/hide/render/destroy 반복 후 자체 HWND와 GDI 객체가 누적되지 않는 실제 Windows 테스트를 포함한다.
- [ ] **Step 6:** 해당 소스·테스트만 stage하고 `fix: add non-activating native taskbar surface`로 commit한다.

### Task 2: DPI에서 직접 그리는 작업표시줄 렌더러

**Files:** Modify `src/ai_mini_monitor/ui/taskbar.py`의 렌더링 함수와 `tests/test_taskbar.py`.

**Interfaces:** `render_taskbar_frame(sensor: SensorSnapshot | None, ai: AIData | None = None, *, items=DEFAULT_TASKBAR_ITEMS, style: str = "icon", dpi_percent: int = 100) -> Image.Image`; `taskbar_frame_size(items=DEFAULT_TASKBAR_ITEMS, style: str = "icon", *, dpi_percent: int = 100) -> tuple[int, int]`. 원래 100% 호출·semantic layers 계약은 유지한다. 폰트 측정과 아이콘 원본 로딩도 목표 DPI에 맞춘다.

- [ ] **Step 1:** `test_taskbar_renderer_draws_at_native_dpi`를 추가한다. 기본 5항목 icon에서 100/150/200/250%의 크기를 각각 `(348,32)/(522,48)/(696,64)/(870,80)`로 assert하고, spy로 숫자 폰트가 `14/21/28/35`, 아이콘이 `18/27/36/45` 픽셀에서 직접 생성됨을 확인한다. `test_high_dpi_credit_text_not_clipped`는 모든 style, `60,518 Credit`와 `999,999,999 Credit`의 마지막 글자가 프레임 내부인지 확인한다. 배경 alpha=0과 글자 최대 alpha=255를 확인한다.
- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_taskbar.py -k 'renderer_draws_at_native_dpi or high_dpi_credit' -q` → DPI 인자 부재로 FAIL 확인.
- [ ] **Step 3:** DPI를 정수 100..800으로 검증하고 bool/범위 밖은 ValueError로 거부한다. 위치·여백·트림에 동일한 배율 반올림을 사용한다. 목표 크기로 로드한 아이콘과 실제 목표 폰트로 그리며 크레딧 폭도 해당 폰트로 측정한다. 전체 프레임 단순 확대는 사용하지 않는다.
- [ ] **Step 4:** 위 두 테스트와 기존 렌더·크레딧·승인 Codex PNG 보존 테스트 → PASS. 100% 프레임과 항목 선택 순서의 기존 계약 유지.
- [ ] **Step 5:** 렌더 관련 diff만 stage하고 `fix: render taskbar readouts at shell DPI`로 commit한다.

### Task 3: 네이티브 바 상태·shell 연동과 앱 호환

**Files:** Modify `src/ai_mini_monitor/ui/taskbar.py`, `tests/test_taskbar.py`, `tests/test_taskbar_integration.py`, `tests/test_taskbar_shell.py`; finalize `src/ai_mini_monitor/ui/taskbar_shell.py`.

**Interfaces:** TaskbarWindow의 기존 생성 인자와 `state: OverlayState`, `visible: bool`, `closed: bool`, `show/hide(*, notify=True)`, `destroy()`, `update_sensor(sensor, ai=None)`, `set_options(items, style)`, `set_position(x, y, *, notify=True) -> tuple[int,int]`, `horizontal_position() -> tuple[float,int]`, `set_horizontal_percent(percent, *, notify=True)`, `nudge_horizontal(delta, *, notify=True)`, `reset_horizontal_position(*, notify=True)`를 유지한다. 마지막 세 위치 메서드는 `tuple[float,int]`를 반환한다. `taskbar_dpi_percent(*, _user32=None) -> int`, `taskbar_should_be_visible(hwnd: int | None = None, *, _user32=None) -> bool`는 읽기 전용이다. Task 1 surface와 Task 2 renderer를 소비한다.

- [ ] **Step 1:** 기존 `_FakeWindow`/Tk factory 의존 테스트를 네이티브 surface fake로 바꾼다. 테스트를 약화하지 말고 `test_native_taskbar_suspend_resume_does_not_activate_readout`의 foreground/실제 IsWindowVisible/동일 HWND 조건을 유지한다. 새 `test_hidden_credit_and_options_resume_latest_frame_or_remain_suppressed`는 숨김 중 큰 Credit·선택 항목·트레이 폭을 바꾼 뒤 공간 부족이면 숨김, 넓어지면 최신 프레임 복귀를 assert한다. `test_drag_clamps_x_and_keeps_taskbar_y`는 음수 모니터와 극단적인 delta에서 x 범위·중앙 y를 assert한다. `test_native_failure_is_contained_by_taskbar_request`는 바 생성/표시 실패가 앱 계정·오버레이 경로를 종료하지 않음을 assert한다.
- [ ] **Step 2:** 아래 집중 명령 → 기존 Tk 바 구현이 native factory/foreground 계약을 충족하지 않아 FAIL 확인.

  `.venv/Scripts/python.exe -m pytest tests/test_native_taskbar.py tests/test_taskbar.py tests/test_taskbar_shell.py tests/test_taskbar_z_order.py tests/test_taskbar_integration.py -q --tb=short`

- [ ] **Step 3:** TaskbarWindow의 OverlayWindow 상속을 제거하고 surface·presenter를 소유한다. root는 UI 루프/monitor fallback에만 사용한다. 캐시 먼저 갱신, DPI와 shell 경계는 수치가 같아도 매 poll 재조회, 렌더 크기에서 geometry 결정, 유효 binding에만 y 중앙/x pre-tray clamp를 적용한다. 마지막 유효 binding/폭의 상대 위치는 일시적으로 shell이 없어도 보존한다. 공개 visible은 사용자의 ON 의도이고 실제 표시 여부는 별도 상태다.
- [ ] **Step 4:** 전체화면/자동 숨김/Explorer 부재/불충분한 폭·높이에서 native hide하고 z-order 복구를 하지 않는다. 복귀 전에 최신 DPI·선택항목·캐시 프레임을 그린다. OFF 상태는 자동 복귀하지 않는다. surface drag는 set_position으로 전달하며 y는 고정한다. 실패 후 native hide/정리하고 오류를 호출자에 전달한다. 기존 app.py 오류 처리로 충분하지 않으면 바 요청 경계만 수정하고 위 실패 격리 테스트로 검증한다.
- [ ] **Step 5:** 집중 테스트 → PASS. 일반 최대화·바탕화면·다른 모니터 전체화면은 숨기지 않고, 동일 모니터 전체화면은 숨김을 확인한다. 기존 환경 변경 테스트에 음수 좌표, 100→200%, 숨김 중 DPI 변경을 포함한다. 사용량 수집/설정 저장은 논리 ON을 기준으로 지속함을 integration 테스트에서 확인한다.
- [ ] **Step 6:** `.venv/Scripts/python.exe -m pytest -q` → 새 기능/기존 기능 전체 PASS. 기존 dist가 소스 fingerprint와 달라 build 관련 테스트가 stale-artifact 이유로 skip되는 경우 그 이유를 기록하고 Task 4의 신선한 빌드 후 0 skips를 필수로 한다.
- [ ] **Step 7:** 변경 파일만 stage하고 `fix: bind native taskbar visibility to live shell state`로 commit한다. 별도 코드 리뷰에서 포인터 수명·DPI·상태 저장·실제 focus 테스트를 검토하고 수정사항을 재검증한다.

### Task 4: 0.2.10 빌드·업데이트 검증·공개 배포

**Files:** Modify `pyproject.toml`, `src/ai_mini_monitor/__init__.py`, `tests/test_release_contract.py`, `README.md`; create release notes and verification receipts under ignored `build/release-v0.2.10/`. Reuse `scripts/Build.ps1`, `scripts/package_release.py`, `scripts/sign_release.py`; updater 소스와 설치된 앱은 변경하지 않는다.

**Interfaces:** 버전 계약은 모두 `0.2.10`. ZIP 고정 이름과 현재 pinned public key를 유지한다. 공개 URL은 `https://github.com/contentriumkorea/mini-monitor/releases/tag/v0.2.10`, ZIP asset URL은 그 tag의 `Mini-Monitor.zip`이다. 기존 0.2.9 UpdateService가 새 release를 찾고 검증해야 한다.

- [ ] **Step 1:** release contract 테스트 기대값을 `0.2.10`으로 바꾸고 단독 실행 → 0.2.9 소스와 불일치 FAIL. 두 버전을 올리고 README에 DPI/전체화면 동작 및 물리 노트북 미검증 경계를 기록한 뒤 같은 테스트 → PASS.
- [ ] **Step 2:** 기존 빌드/서명 스크립트 전체와 QA utility를 읽고 확인한다. `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Build.ps1` → 정상 종료, 두 EXE·BUILD-INFO·SHA256SUMS 생성. SkipTests는 사용하지 않는다. 빌드 후 `.venv/Scripts/python.exe -m pytest -q` → 전체 PASS, 0 skips. fingerprint 대상 파일을 이후 수정하면 재빌드한다.
- [ ] **Step 3:** `.venv/Scripts/python.exe scripts/package_release.py --output-dir build/release-v0.2.10`으로 완전한 ZIP을 만들고, `scripts/sign_release.py --archive build/release-v0.2.10/Mini-Monitor.zip --version 0.2.10 --asset-url https://github.com/contentriumkorea/mini-monitor/releases/download/v0.2.10/Mini-Monitor.zip --output-dir build/release-v0.2.10`을 같은 Python으로 실행한다. 기존 DPAPI signing key만 사용하며 생성/출력하지 않는다. ZIP inventory/hash/signature를 기존 검증 경로로 확인한다.
- [ ] **Step 4:** 기존 `build/qa-frozen-update-029.py`에서 격리 성공·실패 케이스만 재사용해 0.2.9→0.2.10 QA utility를 만든다. 임시 설치 복사본과 별도 LOCALAPPDATA/config, `--no-serial --desktop-smoke`로 실제 frozen 실행/종료·정상 업데이트·새 실행 실패 rollback·정리 정책을 검증한다. 실제 사용 중인 설치/로그인/HKCU Run은 수정하지 않고 사용자 앱도 종료하지 않는다. 실제 설치와 충돌하는 일반 GUI mutex 테스트는 실행하지 않으며, 이를 검증 범위에 명시한다. 필요 시 이전 공개 ZIP을 별도 QA 폴더에 받아 OLD로 사용한다.
- [ ] **Step 5:** 최종 source/release 변경 commit, source fingerprint 일치 확인, main/tag push 후 draft release에 세 고정 asset만 올린다. draft의 asset 이름·ZIP 크기/해시·서명·tag commit을 확인한 뒤 stable/latest 공개한다. 기존 release/tag/assets는 삭제하지 않는다.
- [ ] **Step 6:** 공개 페이지와 세 실제 다운로드 파일을 재확인한다. 0.2.9 소스의 UpdateService로 available/version=0.2.10 및 알림, 다운로드 hash/signature 검증을 확인한다. 서명 변조 fixture는 거부됨을 assert한다. 비공개 receipt/계정자료는 업로드하지 않는다.
- [ ] **Step 7:** 배포 URL, fresh 테스트 수, 실제 네이티브 focus/업데이트·rollback 검증 결과를 보고한다. 자동 테스트·로컬 실제 창·4K 노트북 사용자 검증을 분리하고 미확인 노트북/게임을 검증됐다고 쓰지 않는다. 이 PC 설치 완료라고 보고하지 않는다.

## Execution Handoff

네 Task는 하나의 창 interface를 중심으로 결합되어 있으므로 Native 실행을 권장한다. 구현자는 이 세션에서 작업하고, 끝에 독립 리뷰어가 전체 변경을 확인한다. 사용자 계획 검토와 실행 방식 확인 전에는 구현을 시작하지 않는다.
