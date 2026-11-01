//! Temporary development preview: no account, configuration, device, registry or network access.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        dashboard_data::dashboard_input,
        dashboard_layout::Orientation,
        dashboard_render::DashboardRenderer,
        options_window::{Field, OptionsWindow},
        settings_window::{SettingsAction, SettingsWindow},
    };
    use std::time::{Duration, Instant};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, SetWindowTextW, ShowWindow, TranslateMessage, MSG,
        PM_REMOVE, SW_SHOWNOACTIVATE,
    };
    let window = SettingsWindow::new_hidden()?;
    let mut input = dashboard_input(
        None,
        None,
        None,
        time::OffsetDateTime::now_utc().date(),
        None,
        "",
    );
    for entry in &mut input.entries {
        entry.value = match entry.kind {
            mini_monitor_native::dashboard_layout::CardKind::Cpu => "23%",
            mini_monitor_native::dashboard_layout::CardKind::Ram => "41%",
            mini_monitor_native::dashboard_layout::CardKind::Gpu => "8%",
            mini_monitor_native::dashboard_layout::CardKind::Vram => "18%",
            _ => "87%",
        }
        .into();
        entry.gauge = Some(40.0);
    }
    let frame = DashboardRenderer::new().render(&input, Orientation::Landscape, false, None)?;
    window.set_preview(frame)?;
    let (mut monitor, mut overlay, mut taskbar) = (false, false, false);
    window.set_output_state(monitor, overlay, taskbar)?;
    let title: Vec<u16> = "Mini Monitor · 디자인 미리보기 (DEMO)"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        SetWindowTextW(window.handle(), title.as_ptr());
        ShowWindow(window.handle(), SW_SHOWNOACTIVATE);
    }
    let mut form: Option<OptionsWindow> = None;
    if let Some(id) = std::env::args().nth(1).and_then(|arg| match arg.as_str() {
        "--overlay" => Some(12),
        "--taskbar" => Some(13),
        "--api" => Some(9),
        _ => None,
    }) {
        unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::SendMessageW(
                window.handle(),
                windows_sys::Win32::UI::WindowsAndMessaging::WM_COMMAND,
                id,
                0,
            );
        }
    }
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(600) {
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            if form
                .as_ref()
                .is_some_and(|f| f.translate_message(&mut message))
            {
                continue;
            }
            if message.message == windows_sys::Win32::UI::WindowsAndMessaging::WM_KEYDOWN
                && message.wParam == 9
                && unsafe {
                    windows_sys::Win32::UI::WindowsAndMessaging::IsDialogMessageW(
                        window.handle(),
                        &message,
                    )
                } != 0
            {
                continue;
            }
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        while let Some(action) = window.take_action() {
            match action {
                SettingsAction::ToggleMonitor => monitor = !monitor,
                SettingsAction::ToggleOverlay => overlay = !overlay,
                SettingsAction::ToggleTaskbar => taskbar = !taskbar,
                SettingsAction::Quit => return Ok(()),
                SettingsAction::TaskbarSettings
                | SettingsAction::OverlaySettings
                | SettingsAction::ApiSettings => {
                    let (title, fields) = match action {
                        SettingsAction::TaskbarSettings => (
                            "작업표시줄 바 설정 · DEMO",
                            vec![
                                Field::Check("CPU", true),
                                Field::Check("RAM", true),
                                Field::Check("GPU", true),
                                Field::Check("VRAM", true),
                                Field::Check("Codex", true),
                                Field::Check("OpenAI API", false),
                                Field::Choice(
                                    "표시 방식",
                                    vec!["텍스트 + %", "아이콘 + %", "아이콘 + 텍스트 + %"],
                                    1,
                                ),
                                Field::Slider("좌우 위치", 0, 100, 80),
                            ],
                        ),
                        SettingsAction::OverlaySettings => (
                            "PC 상태창 설정 · DEMO",
                            vec![
                                Field::Slider("화면 크기 (%)", 50, 200, 100),
                                Field::Slider("배경 불투명도 (%)", 0, 100, 70),
                            ],
                        ),
                        _ => (
                            "OpenAI API 사용량 설정 · DEMO",
                            vec![
                                Field::Check("OpenAI API 모니터링", true),
                                Field::Edit("총액 (USD)", "100".into(), false),
                                Field::Edit("시작일 (YYYY-MM-DD)", "2026-11-01".into(), false),
                                Field::Edit("관리자 API 키 (샘플 입력)", "".into(), true),
                            ],
                        ),
                    };
                    form = Some(OptionsWindow::new(window.handle(), title, fields)?);
                    unsafe {
                        ShowWindow(form.as_ref().unwrap().handle(), SW_SHOWNOACTIVATE);
                    }
                }
                _ => {}
            }
            window.set_output_state(monitor, overlay, taskbar)?;
        }
        if let Some(values) = form.as_ref().map(|f| f.take_apply()).transpose()?.flatten() {
            drop(values);
            form.as_ref()
                .unwrap()
                .message("미리보기 적용됨 · 실제 설정은 변경하지 않습니다.");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
