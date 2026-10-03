# Mini Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** VRAM·공식 Codex 계정·가독성 개선·안전한 업데이트를 갖춘 Mini Monitor를 검증하고 공개 GitHub Release까지 배포한다.

**Architecture:** 기존 Python/Pillow/Tk와 센서·직렬 파이프라인을 유지한다. 앱 소유 Codex 서비스와 업데이트 서비스는 비동기 작업·불변 스냅샷으로 UI에 연결하고, 실행 파일 교체는 검증된 별도 helper가 정상 종료 후 수행한다. 공개 소스 기준 커밋 이후 GPU·Codex·UI를 파일 단위로 병렬 구현하고 통합·업데이트·배포를 검증한다.

**Tech Stack:** Python >=3.10,<3.14, 기존 잠금 의존성, Pillow, Tk, LibreHardwareMonitor, 공식 Codex CLI app-server, PyInstaller onedir, Ed25519 검증용 `cryptography`, Windows PowerShell, GitHub Releases.

**Spec:** `docs/superpowers/specs/2026-10-03-mini-monitor-design.md` (209줄 설계). 2026-10-03 사용자가 설계 전체와 구현·검토·공개 배포 연속 진행을 승인했다. 이전 설계서의 검토 대기 문구는 이 최신 승인으로 해소되며 추가 승인 대기를 만들지 않는다.

## Global Constraints

- 기획·설계·최종 디자인 검토는 astra max, 모든 실제 구현·수정·빌드·배포 작업은 사용자가 지정한 gpt-6-sol high가 수행한다.
- 작업 루트는 현재 `AI-Mini-Monitor`뿐이다. 형제 `35inchENG`, 기존 배포본, 실제 COM 장치는 자동 테스트·설계 검증 대상이 아니다.
- 내부 `ai_mini_monitor`, 사용자 데이터 `AI-Mini-Monitor`, 기존 단일 인스턴스 mutex를 유지한다. 표시명은 `Mini Monitor`, EXE는 `Mini-Monitor.exe`/`Mini-Monitor-CLI.exe`이다.
- 두 화면은 480×320·320×480의 실제 좌표로 렌더한다. 제목 >=18px, 숫자 42/40px, `%` 21/20px, 게이지 높이 >=10/8px, 값 그룹 x+112 및 기준선 y+80/73을 지킨다.
- VRAM은 같은 GPU의 전용 used/total만, 전력은 지원 시만 표시한다. 미상은 0이 아니다. 클럭·팬·새 GPU 선택 UI는 제외한다.
- Codex는 격리 `user_data_dir()/codex-home`와 공식 CLI만 사용한다. canonical `codex` 버킷·최장 창 주 값·동적 라벨·120초 지연 표시를 지키며 기본 홈·토큰 파일·개인 API를 읽지 않는다.
- 배경 불투명도 0–100%만 적용하고 오버레이 전경 불투명·항상 위·드래그·위치 복원, 밝기 1–50·방향·자동 실행·USB 프로토콜을 보존한다.
- 업데이트는 사용자 클릭 후 서명·해시·경로 검증을 통과한 onedir만 교체한다. 강제 종료·권한 상승·사용자 데이터 변경·기본 Codex 로그아웃은 금지한다.
- 공개 목적지는 `contentriumkorea/mini-monitor`; 배포 기준 버전은 `0.2.0`으로 일치시킨다. 최종 ZIP만 `YYYY-MM-DD HH'mm Mini Monitor.zip`이며 내부 파일·태그·URL은 날짜로 바꾸지 않는다.

## Review Focus

1. Windows ZIP의 대소문자 중복·ADS·장치명·reparse·경로 탈출: 설치 밖 쓰기 없이 거부해야 한다 → Task 4 `test_windows_archive_aliases_are_rejected`.
2. 검증 후 변경된 설치 파일·설치 안 사용자 설정·살아 있는 프로세스: 원본/백업을 보존하고 수동 복구로 전환해야 한다 → Task 4 `test_changed_install_and_live_process_never_swap`.
3. 공백/한글 경로의 CLI·상속 인증 환경·계정 전환 중 늦은 응답: 셸 주입·다른 계정 사용·오래된 수치 게시가 없어야 한다 → Task 2 `test_isolated_cli_and_late_generation_response`.
4. 같은 이름의 GPU 두 개·하나만 누락된 VRAM 센서·충돌 설정: 식별자로 같은 어댑터를 유지하고 짝을 섞지 않아야 한다 → Task 1 `test_identical_gpu_names_do_not_merge_adapters`.
5. 고DPI·짧은 작업 영역·음수 모니터 좌표·불투명도 0: 로그인/종료 버튼과 전체 드래그를 계속 사용할 수 있어야 한다 → Task 3 `test_compact_dpi_and_transparent_overlay_remain_usable`.

---

## 파일 소유권과 실행 순서

`P = src/ai_mini_monitor/`로 표기한다. 표는 편집 권한 계약이며 공유 파일을 임의로 가져가지 않는다.

| 담당 | 단독 편집 파일 |
|---|---|
| 통합 구현 담당 | `P/models.py`, `P/config.py`, `config.example.json`, `P/app.py`, `P/controller.py`, `P/desktop_session.py`, `P/cli.py`, `P/autostart.py`, `P/resources.py`, `P/ui/tray.py`, Task 4·6 파일, 나머지 통합 테스트 |
| GPU 담당 | `P/sensors/libre_hardware.py`, `P/sensors/collector.py`, `P/state.py`, `tests/test_sensors.py`, `tests/test_smoothing.py` |
| Codex 담당 | 신규 `P/ai/codex_account.py`, 신규 `tests/test_codex_account.py`, 레거시 표기만 바꾸는 `P/ai/codex_usage.py` |
| UI 담당 | `P/rendering/{layout,renderer,theme,fonts}.py`, `P/ui/setup.py`, `P/demo.py`, `P/preview.py`, `tests/test_{layout,renderer,setup,forbidden_content,preview}.py` |

- [ ] 통합 담당은 허용 목록·개인정보 검사 후 초기 제품 소스 기준 커밋과 승인된 공개 저장소를 준비한다. `.venv`, 캐시, diagnostics, baseline, preview-smoke, 인증 자료, 기존 build/dist는 넣지 않고 PyInstaller `.spec`은 명시적으로 포함한다.
- [ ] Task 1의 공유 계약을 통합 담당이 먼저 반영·공지한 뒤 Task 1 GPU 부분, Task 2, Task 3을 병렬화한다. 이후 Task 4·5·6을 통합한다. 각 작업 끝에 새 검토자가 사양·정확성·보안·과설계 여부를 검토한다.
- [ ] 커밋은 통합 담당만 `git add <해당 작업의 명시 파일 목록>`으로 수행한다. `git add .`를 사용하지 않으며 미완료 다른 담당 파일을 함께 커밋하지 않는다.
- [ ] 모든 아래 명령은 프로젝트 루트의 PowerShell에서 실행한다. 테스트용 process/network/registry/serial은 fake로 대체하며 실계정·실장치 성공으로 보고하지 않는다.
- 기준 소스는 `feat/mini-monitor-release`의 보존 커밋 `b076637`이다. 기존 checkout에서 진행하며 추가 worktree 이동은 하지 않는다. 기준 전체 테스트는 649 PASS·1 stale-artifact SKIP·49 Tk 초기화 ERROR로, 새 프로세스의 Tk 8.6.15 시작은 성공하므로 환경 재설치가 아닌 테스트 격리 문제를 Task 3에서 진단한다.

### Task 1: 공유 계약과 같은 GPU의 VRAM 파이프라인

**Files:** Modify `P/models.py`, `P/config.py`, `config.example.json`(통합 담당), GPU 소유 파일. Test `tests/test_config.py`, `tests/test_sensors.py`, `tests/test_smoothing.py`.

**Interfaces:**
- 공유 계약: `AIProviderKind.CODEX_ACCOUNT = "codex_account"`, `AIConfig.codex_cli_path: str | None = None`; 기존 설정 역직렬화와 키를 보존한다.
- `SensorReading.hardware_identifier: str | None = None`; `SensorSnapshot`/`DisplaySnapshot` 끝에 `gpu_vram_used_gib`, `gpu_vram_total_gib`, `gpu_power_w`를 기본값 있는 `Metric`로 추가한다.
- 기존 `SystemSensorCollector.sample() -> SensorSnapshot`, `DisplayComposer.compose(values, *, monotonic_now) -> DisplaySnapshot`의 호출 계약은 그대로 둔다.

- [ ] **Red: 기본값·동일 어댑터 테스트를 먼저 작성한다.**
```python
def test_new_gpu_metrics_default_to_unknown():
    snapshot = DisplaySnapshot()
    assert snapshot.gpu_vram_used_gib.value is None
    assert snapshot.gpu_vram_total_gib.value is None
    assert snapshot.gpu_power_w.value is None
```
- [ ] `test_identical_gpu_names_do_not_merge_adapters`: 이름이 같은 `/gpu/0`, `/gpu/1` fixture에서 `assert used.value == 8.0`; total은 동일 ID의 16.0만 허용하고 다른 GPU의 24.0을 쓰지 않는다. 충돌 온도 설정은 `assert temperature.value is None`.
- [ ] RED 실행: `.\.venv\Scripts\python.exe -m pytest tests/test_sensors.py tests/test_smoothing.py tests/test_config.py -q`; 신규 필드/선택 동작 부재로 실패하는지 확인한다.
- [ ] **Green:** 공유 모델·설정은 통합 담당, 센서는 GPU 담당이 구현한다. configured load→configured temperature→결정적 외장 GPU 순으로 선택하고 LHM 정확한 Used/Total SmallData 쌍을 1024로 나눈다. 같은 어댑터 D3D Dedicated 쌍만 대체하고 `GPU Package` 전력만 허용한다.
- [ ] shared memory·mixed pair·음수/NaN/0 total·virtual GPU·missing power·한 수집 주기 테스트를 추가한다. 상태 조합은 새 Metric을 그대로 전달하고 별도 폴러를 만들지 않는다.
- [ ] GREEN: 같은 테스트 명령이 모두 PASS. 코어 수집 회귀를 확인하고 통합 담당이 `feat: add same-adapter GPU memory metrics`로 해당 파일만 커밋한다.

### Task 2: 공식 Codex 계정 서비스

**Files:** Create `P/ai/codex_account.py`, `tests/test_codex_account.py`; Modify `P/ai/codex_usage.py`의 명시적 레거시 제목만. `models.py`/`app.py`를 수정하지 않는다.

**Interfaces:**
- `CodexLimitWindow(duration_mins: int, used_percent: float, resets_at: datetime | None)`는 불변이다. `remaining_percent`는 검증된 값에 대해서만 `100-used_percent`이다.
- `CodexAccountSnapshot(generation: int, state: str, email: str | None, plan_type: str | None, login_pending: bool, windows: tuple[CodexLimitWindow, ...], updated_at: datetime | None, error_detail: str | None, ai: AIData)`는 불변이며 비밀을 담지 않는다.
- `CodexAccountEvent(kind: str, generation: int, auth_url: str | None = None)`와 `CodexAccountService(*, cli_path: Path | None, home: Path, refresh_seconds: int = 60)`를 제공한다.
- 서비스의 `begin_login/cancel_login/refresh/logout() -> bool`은 enqueue 성공 여부, `set_polling(enabled: bool) -> None`, `snapshot() -> CodexAccountSnapshot`, `drain_events() -> tuple[CodexAccountEvent, ...]`, `close() -> None`이다. URL 이벤트는 한 번 배출한 뒤 보관하지 않는다.

- [ ] **Red:** fake stdio 프로세스로 init 성공→initialized 순서·요청 ID·창 선택 테스트를 작성한다.
```python
def test_longest_codex_window_is_primary(service_with_fake_cli):
    service = service_with_fake_cli(codex_used=[(300, 38), (10080, 21)])
    assert service.snapshot().ai.primary_value == "79%"
    assert service.snapshot().ai.primary_label == "7D LEFT"
```
- [ ] `test_isolated_cli_and_late_generation_response`: 한글/공백 executable argv, `cwd == home`, child `CODEX_HOME == home`, 인증 환경 변수 제거를 assert하고 generation 변경 뒤 이전 응답이 `ai`를 바꾸지 않는지 확인한다.
- [ ] RED: `.\.venv\Scripts\python.exe -m pytest tests/test_codex_account.py -q`; 모듈/서비스 부재의 실패를 확인한다.
- [ ] **Green:** signed CLI 발견·지정 경로 확인, 중립 cwd/정제 env, 단일 lazy subprocess/reader, 60초 poll·최대 300초 backoff, 취소·bounded close를 구현한다. 인증 URL은 HTTPS/no-userinfo와 정확한 두 공식 호스트만 허용한다.
- [ ] canonical `codex` 선택·legacy 제한·null/미상·dynamic windows·부분 알림 전체 재조회·계정 변화 즉시 clear·120초 stale·API-key 계정 거부·missing CLI·서명 실패·종료/타임아웃 테스트를 완성한다. 브라우저·서명 확인·subprocess는 테스트에서 patch한다.
- [ ] GREEN: `.\.venv\Scripts\python.exe -m pytest tests/test_codex_account.py tests/test_codex_usage.py tests/test_redaction.py -q`가 PASS. 검토 후 통합 담당이 `feat: connect isolated Codex account service`로 커밋한다.

### Task 3: 네이티브 렌더러와 설정창

**Files:** UI 소유 파일만. 오버레이 핵심 파일은 수정하지 않고 기존 alpha contract를 소비한다.

**Interfaces:**
- 기존 `DashboardRenderer.render(DisplaySnapshot) -> Image.Image`와 `get_overlay_layers(image)` 유지; `last_placements/last_gauges/last_sparklines`는 검증에 계속 제공한다.
- `SetupWindow`에 선택적 콜백 `on_codex_login/on_codex_cancel/on_codex_logout/on_codex_disconnect/on_update_check/on_update_apply: Action | None`, `on_codex_cli_selected: Callable[[Path], ActionResult] | None`를 추가한다.
- 공개 갱신 메서드 `update_codex_account(snapshot: CodexAccountSnapshot) -> None`, `update_update_status(snapshot: UpdateSnapshot) -> None`는 Tk 메인 스레드에서만 호출한다. 기존 콜백/생성자 기본값을 보존한다.

- [ ] **Red:** 두 방향 geometry와 title/value/gauge/alpha 테스트를 작성한다.
```python
def test_dashboard_keeps_readable_native_lanes(rendered_landscape):
    renderer, image = rendered_landscape
    assert image.size == (480, 320)
    assert renderer.last_placements["gpu_label"].font_size >= 18
    assert renderer.last_placements["gpu_value_digits"].font_size == 42
    assert renderer.last_gauges["gpu"].track.height >= 10
    assert not renderer.clipping_issues
```
- [ ] `test_compact_dpi_and_transparent_overlay_remain_usable`: 125/150/200% DPI·짧은 work area·negative x fixture에서 핵심 버튼이 접근 가능하고 opacity 0에도 foreground alpha 255·외곽 alpha 0·drag가 유지됨을 확인한다.
- [ ] RED: `.\.venv\Scripts\python.exe -m pytest tests/test_layout.py tests/test_renderer.py tests/test_setup.py tests/test_forbidden_content.py -q`; 기대 geometry/가독성 차이로 실패해야 한다.
- [ ] 기존 Tk 오류를 앞선 UI 테스트→setup 순서로 재현하고 fixture의 root/interpreter/ttk 정리·전역 상태 격리를 최소 수정한다. 실제 `tk.tcl`·`ttk.tcl`은 존재하므로 Python 재설치로 우회하지 않는다. 순서 의존 회귀 테스트와 신선한 프로세스 검사 둘 다 통과시킨다.
- [ ] **Green:** spec의 가로228×134/세로304×103 네 카드와 20px header, 정확한 앵커, 18px 제목, VRAM 상시 줄, 선택 전력, 분리된 10/8px 게이지·작은 그래프를 구현한다. CODEX는 최장 창 주 값·짧은 창 보조 값이며 작은 카드 5H reset은 넣지 않는다.
- [ ] 설정창의 계정/고급 옵션·고정 높이 업데이트 배너·계정 작업 버튼 상태를 구현한다. 로그인/사용량은 serial Start에 묶지 않는다. DEMO를 명시한 오류/미상/최대 수치 fixture와 네 방향 미리보기를 생성할 수 있게 한다.
- [ ] GREEN: `.\.venv\Scripts\python.exe -m pytest tests/test_layout.py tests/test_renderer.py tests/test_setup.py tests/test_overlay.py tests/test_layered_window.py tests/test_orientation.py tests/test_preview.py -q`가 PASS. 통합 담당이 `feat: refresh Mini Monitor dashboard and controls`로 커밋한다.

### Task 4: 서명된 업데이트 확인·staging·교체 helper

**Files:** Create `P/updater.py`, `scripts/Apply-Update.ps1`, `scripts/sign_release.py`, `assets/update-public-key.pem`, `tests/test_updater.py`, `tests/test_update_helper.py`; Modify dependency/lock/license files only by integration owner.

**Interfaces:**
- 불변 `UpdateManifest(version: str, channel: str, archive_url: str, archive_sha256: str, archive_size: int, files: tuple[tuple[str, str], ...])`, `PreparedUpdate(install_root: Path, staged_root: Path, journal_path: Path, helper_path: Path)`를 정의한다.
- `UpdateSnapshot(state: str, version: str | None, release_url: str | None, message: str, prepared: PreparedUpdate | None)`의 state는 `idle/checking/available/preparing/ready/manual_required/error`이다.
- `verify_release_manifest(payload: bytes, signature: bytes, public_key: bytes) -> UpdateManifest`, `stage_update(manifest: UpdateManifest, *, install_root: Path, config_path: Path | None) -> PreparedUpdate`, `launch_update_helper(prepared: PreparedUpdate, *, parent_pid: int) -> bool`를 제공한다.
- `UpdateService(*, current_version: str, install_root: Path | None, config_path: Path | None)`의 `check(*, force: bool = False) -> bool`, `prepare() -> bool`는 비동기 enqueue; `snapshot() -> UpdateSnapshot`, `close() -> None`는 Task 5가 사용한다.

- [ ] **Red:** 로컬 fake HTTP·임시 설치 트리로 서명/버전/manifest·zip 테스트를 작성한다.
```python
def test_tampered_manifest_is_rejected(signed_release):
    payload, signature, public_key = signed_release
    with pytest.raises(ValueError):
        verify_release_manifest(payload + b" ", signature, public_key)
```
- [ ] Review Focus 테스트에 `../x`, `C:/x`, `a:stream`, `CON`, `a`/`A`, reparse fixture 거부를 넣는다. 설치 안 custom config·알 수 없는/변경된 파일·새 앱 alive ACK timeout에서 `assert old_tree_unchanged` 또는 `assert backup_exists` 및 `assert forced_kills == []`를 검증한다.
- [ ] RED: `.\.venv\Scripts\python.exe -m pytest tests/test_updater.py tests/test_update_helper.py -q`; 미구현 검증/교체 동작으로 실패해야 한다.
- [ ] **Green:** cryptography 버전을 공식 배포에서 확인해 잠그고 Ed25519 raw canonical JSON bytes를 검증한다. 서명은 sorted keys·compact separators·UTF-8·중복키 없음의 schema_version=1 문서에 적용한다. 기본 repo/channel은 승인된 mini-monitor/stable로 고정한다.
- [ ] 최초 시작 조회·86400초 간격·버전당 한 번 알림·수동 조회·오프라인 무해 동작을 구현한다. 15초 요청 timeout, metadata 1MiB, ZIP 1GiB, 해제 3GiB/20000파일을 상한으로 두고 signed URL·정확한 GitHub asset host만 허용한다.
- [ ] staging·원본 설치 manifest 대조·검증 직후 변경 재검사·helper 자기 해시 검증·별도 argv/숨김 실행을 구현한다. helper는 PID 종료 최대60초 후 rename+저널+재시작, ACK 최대60초를 처리한다. 앱이 살아 있으면 graceful close를 요청하고 실패 시 백업을 유지한다.
- [ ] `scripts/sign_release.py`는 `--archive --version --asset-url --key --output-dir`을 받는다. private key는 프로젝트 밖 현재 사용자 보호 저장소에 생성·보관하고 출력/커밋하지 않는다. public key는 baseline에 고정하며 서명 준비 전 자동 적용은 비활성화한다.
- [ ] GREEN: 위 두 테스트 모듈과 `tests/test_environment_integrity.py tests/test_licenses.py` PASS. 검토 후 `feat: add verified portable app updates`로 커밋한다.

### Task 5: 앱 수명·이름 변경·호환 통합

**Files:** Integration 소유 `P/app.py`, `P/controller.py`, `P/desktop_session.py`, `P/cli.py`, `P/autostart.py`, `P/resources.py`, `P/ui/tray.py`, `tests/test_{app,controller,desktop_session,autostart,cli,tray,single_instance}.py`.

**Interfaces:** Task 2·4 서비스를 앱당 하나씩 생성한다. `MonitorController.__init__`에 선택적 `codex_account_snapshot: Callable[[], CodexAccountSnapshot] | None = None`만 주입하고 별도 프로세스를 만들지 않는다. 기존 session start 인수 전달은 호환 기본값으로 확장한다.

- [ ] **Red:** fake 서비스·serial writer로 로그인과 시작/중지 수명 테스트를 작성한다.
```python
def test_login_without_monitor_start(desktop_harness):
    app = desktop_harness()
    app.click_codex_login()
    assert app.codex.begin_login_calls == 1
    assert app.serial_open_calls == 0
    assert app.sensor_start_calls == 0
```
- [ ] 반복 start/stop에 서비스 생성1회·poll 토글·진행 로그인 유지, quit에서비스close1회, update ready 전 미종료, helper 실패 시 미종료, 알 수 없는 Run 값 보존 테스트를 추가한다.
- [ ] RED: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_controller.py tests/test_autostart.py tests/test_cli.py -q`; 새 행동 부재로 실패해야 한다.
- [ ] **Green:** Tk poll에서 계정 이벤트/스냅샷·업데이트 상태를 반영한다. 명시적 apply 후 ready가 된 경우 helper launch 성공→기존 request_exit 순서로 진행한다. 외부 활동/기본로그아웃과 연결해제를 분리하고 legacy fallback을 금지한다.
- [ ] CLI에 내부 `--update-ack` 경로·`--update-token`을 추가해 핵심 UI/assets 초기화 직후에만 토큰 ACK를 쓴다. 경로는 현재 업데이트 staging 아래로 검증하며 COM/로그인 성공을 기다리지 않는다. 새 실행의 custom config/기존 실행 인수는 보존한다.
- [ ] 제품 표시명·트레이·EXE 경로를 새 이름으로 바꾸고 기존 mutex/data/package를 유지한다. opt-in이 검증된 현재 앱 Run 값만 새 명령으로 이관하며 실패 시 원래 값을 보존한다.
- [ ] GREEN: `.\.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_controller.py tests/test_desktop_session.py tests/test_autostart.py tests/test_cli.py tests/test_tray.py tests/test_single_instance.py tests/test_power_events.py -q` PASS. `feat: integrate account and updater lifecycle`로 커밋한다.

### Task 6: 패키징·서명·시각 QA·공개 Release

**Files:** Modify `AI-Mini-Monitor.spec`(내부 이름 유지), `scripts/{Build,Run,Diagnose,Render-Previews,Set-OpenAIKey}.ps1`, `scripts/verify_environment.py`, `pyproject.toml`, `P/__init__.py`, `requirements.lock`, `.gitignore`, `README_KO.md`, `TEST_RESULTS.md`, `THIRD_PARTY_*`, `LICENSES/*`, `SOURCE-OFFER.md`, `tests/test_packaging_config.py`; Create `tests/test_release_contract.py`.

**Interfaces:** Release `v0.2.0`, runtime `0.2.0`, folder `dist/Mini-Monitor`; signed assets `update-manifest.json`·`update-manifest.sig`, timestamped ZIP, SHA256SUMS, corresponding source. `scripts/sign_release.py`의 Task 4 계약을 사용한다.

- [ ] **Red:** 배포 계약 테스트를 먼저 작성한다.
```python
def test_release_versions_and_program_names_match(release_inputs):
    assert release_inputs.project_version == release_inputs.runtime_version == "0.2.0"
    assert release_inputs.desktop_name == "Mini-Monitor.exe"
    assert release_inputs.cli_name == "Mini-Monitor-CLI.exe"
```
- [ ] RED: `.\.venv\Scripts\python.exe -m pytest tests/test_release_contract.py tests/test_packaging_config.py -q`; 기존 이름/버전 불일치로 실패 확인 후 새 배포 경로·source bundle·helper/public key 포함·환경 closure/라이선스 자료를 갱신한다.
- [ ] 전체 GREEN: `.\.venv\Scripts\python.exe -m pytest -q`; 모든 테스트 PASS 후 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Build.ps1`를 실행하고 BUILD-INFO·SHA256SUMS·GUI/CLI PE·필수 리소스를 검사한다.
- [ ] `dist\Mini-Monitor\Mini-Monitor-CLI.exe --render-previews build\qa\previews`로 두 방향/네 회전·최대값·unknown·VRAM·Codex 상태를 만든다. 원래 픽셀 크기 이미지·setup 고DPI·overlay0/50/100 캡처를 디자인 담당이 육안 검수하고 자르기·충돌을 해결한다.
- [ ] `dist\Mini-Monitor\Mini-Monitor.exe --no-serial --desktop-smoke 8`의 정상 종료를 확인한다. GPU 실제 수치는 센서 읽기 결과와 비교할 수 있지만 COM 연결은 하지 않는다. 실로그인은 사용자 브라우저 인증 없으면 미검증으로 정확히 기록한다.
- [ ] 임시 설치 두 버전과 signed fixture release로 다운로드→검증→정상 종료→교체→ACK 및 실패 복구를 검증한다. 이전 설치/사용자 데이터 해시, custom-config 거부, unknown-file 거부, Run opt-in 보존을 확인하고 테스트 stable을 공개하지 않는다.
- [ ] 공개 허용 목록·secret/PII 검사·라이선스·README 링크·실측 테스트 결과를 점검한다. 현재 runtime manifest에 없는 사용자 파일과 diagnostics/baseline/개인 경로를 제외하고 서명 개인키가 git/ZIP 어느 곳에도 없음을 검사한다.
- [ ] 정확한 최종 ZIP과 `update-manifest.json`/`update-manifest.sig`를 생성한다. pinned public key로 최종 메타데이터 서명을 재검증하고 ZIP 전체 해시·파일 manifest를 재검증한다. 서명 키 운영 또는 두 버전 테스트가 실패하면 자동 업데이트 완료를 주장하지 않는다.
- [ ] 통합 담당이 최종 검토를 반영해 `release: publish Mini Monitor 0.2.0` 커밋·태그를 만들고 승인 저장소에 push한다. GitHub Release는 자산을 올리고 검증할 때까지 draft로 두며 검증 후 stable 공개한다.
- [ ] 공개 저장소·Release·실제 다운로드 파일을 다시 확인하고 다운로드 SHA256이 로컬 최종 ZIP과 같은지 검사한다. 재현 가능한 소스·라이선스·미서명 Windows 앱 경고·기준 버전의 1회 수동 설치·실계정/실장치 검증 한계를 Release에 명시한다.
- [ ] 최종 응답은 GitHub/Release 링크·타임스탬프 ZIP 경로·실제 테스트 결과·남은 검증 한계만 간결히 전달한다. 코드/빌드/다운로드만 성공한 상태를 배포 완료로 표현하지 않는다.

## 계획 자체 검토와 실행 인계

- [ ] 부모는 spec §1–11이 Task 1–6에 대응하고 Review Focus 5개에 명명된 테스트가 있는지 확인한다. 서비스/스냅샷/콜백 이름 변경은 관련 담당자에게 먼저 알리고 문서·테스트를 함께 맞춘다.
- [ ] 각 task의 RED→GREEN 증거와 검토 결과를 남기고, 최종 전체 코드 검토·보안 검토·100% 픽셀 디자인 검토 뒤 Release를 공개한다. 추가 사용자 승인 대기는 없으며 새 권한·실제 로그인 상호작용이 필요한 경우에만 그 한계를 보고한다.
