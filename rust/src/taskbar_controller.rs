//! Connects shell geometry, transparent rendering and a non-activating window.
use crate::{
    layered_window::present,
    taskbar_geometry::{place_horizontal, Rect},
    taskbar_render::{MetricKind, TaskbarFrame, TaskbarRenderer, TaskbarStyle},
    taskbar_shell::{fullscreen_on_monitor, query_primary_taskbar, FullscreenState},
    taskbar_window::TaskbarWindow,
};
use std::{io, path::PathBuf};
use windows_sys::Win32::Foundation::HWND;

#[derive(Clone, Copy)]
pub struct ShellState {
    pub bar: Rect,
    pub notification: Option<Rect>,
    pub monitor: Rect,
    pub dpi: u32,
    pub visible: bool,
    pub fullscreen: FullscreenState,
}

// No Debug: render key and frame may contain private usage values.
struct FrameKey {
    dpi: u32,
    style: TaskbarStyle,
    items: Vec<(MetricKind, String)>,
}
impl FrameKey {
    fn matches(&self, dpi: u32, style: TaskbarStyle, items: &[(MetricKind, &str)]) -> bool {
        self.dpi == dpi
            && self.style == style
            && self.items.len() == items.len()
            && self
                .items
                .iter()
                .zip(items)
                .all(|((a, b), (c, d))| a == c && b == d)
    }
}

pub struct TaskbarController {
    window: TaskbarWindow,
    renderer: TaskbarRenderer,
    style: TaskbarStyle,
    assets: Option<PathBuf>,
    desired_x: i32,
    key: Option<FrameKey>,
    frame: Option<TaskbarFrame>,
    bounds: Option<Rect>,
    visible: bool,
    position_change: Option<(i32, i32)>,
    pending_drag_position: Option<(i32, i32)>,
}
impl TaskbarController {
    pub fn new(style: TaskbarStyle, assets: Option<PathBuf>, desired_x: i32) -> io::Result<Self> {
        Ok(Self {
            window: TaskbarWindow::new_hidden()?,
            renderer: TaskbarRenderer::new(),
            style,
            assets,
            desired_x,
            key: None,
            frame: None,
            bounds: None,
            visible: false,
            position_change: None,
            pending_drag_position: None,
        })
    }
    pub fn handle(&self) -> HWND {
        self.window.handle()
    }
    pub fn last_bounds(&self) -> Option<Rect> {
        self.bounds
    }

    /// Placement is clamped against the current taskbar on the next tick.
    pub fn set_desired_x(&mut self, x: i32) {
        self.desired_x = x;
    }

    pub fn set_style(&mut self, style: TaskbarStyle, assets: Option<PathBuf>) {
        self.style = style;
        self.assets = assets;
        self.key = None;
    }

    /// UI code may persist the latest clamped position after a drag.
    pub fn take_position_change(&mut self) -> Option<(i32, i32)> {
        self.position_change.take()
    }

    /// Read-only Explorer/foreground query, followed by changes only to this app's window.
    pub fn tick(&mut self, items: &[(MetricKind, &str)]) -> io::Result<()> {
        let shell = match query_primary_taskbar() {
            Ok(value) => value,
            Err(error) => {
                self.hide();
                return Err(error);
            }
        };
        let fullscreen = fullscreen_on_monitor(shell.monitor, Some(self.window.handle()));
        self.tick_with_shell(
            items,
            ShellState {
                bar: shell.bar,
                notification: shell.notification,
                monitor: shell.monitor,
                dpi: shell.dpi,
                visible: shell.visible,
                fullscreen,
            },
        )
    }

    pub fn tick_with_shell(
        &mut self,
        items: &[(MetricKind, &str)],
        shell: ShellState,
    ) -> io::Result<()> {
        if !shell.visible || shell.fullscreen != FullscreenState::NotCovered {
            self.hide();
            return Ok(());
        }
        let previous_bounds = self.bounds;
        let delta = self.window.take_drag_delta();
        let drag_finished = self.window.take_drag_finished();
        if delta != 0 {
            if let Some(previous) = previous_bounds {
                self.desired_x = previous.left.saturating_add(delta);
            }
        }
        let changed = self
            .key
            .as_ref()
            .is_none_or(|key| !key.matches(shell.dpi, self.style, items));
        if changed {
            let frame =
                match self
                    .renderer
                    .render(items, shell.dpi, self.style, self.assets.as_deref())
                {
                    Ok(value) => value,
                    Err(error) => {
                        self.hide();
                        return Err(error);
                    }
                };
            self.key = Some(FrameKey {
                dpi: shell.dpi,
                style: self.style,
                items: items
                    .iter()
                    .map(|(kind, value)| (*kind, (*value).to_owned()))
                    .collect(),
            });
            self.frame = Some(frame);
        }
        let Some(frame) = self.frame.as_ref() else {
            self.hide();
            return Err(io::Error::other("taskbar frame unavailable"));
        };
        let Some(mut bounds) = place_horizontal(
            shell.bar,
            shell.notification,
            frame.width as i32,
            self.desired_x,
            2,
        ) else {
            self.hide();
            return Ok(());
        };
        let bar_height = i64::from(shell.bar.bottom) - i64::from(shell.bar.top);
        if i64::from(frame.height) > bar_height {
            self.hide();
            return Ok(());
        }
        let y = i64::from(shell.bar.top) + (bar_height - i64::from(frame.height)) / 2;
        bounds.top = i32::try_from(y).map_err(|_| io::Error::other("taskbar position invalid"))?;
        bounds.bottom = i32::try_from(y + i64::from(frame.height))
            .map_err(|_| io::Error::other("taskbar position invalid"))?;
        if changed || self.bounds != Some(bounds) || !self.visible {
            if let Err(error) = present(
                &self.window,
                bounds.left,
                bounds.top,
                frame.width,
                frame.height,
                &frame.pixels,
            ) {
                self.hide();
                return Err(error);
            }
        }
        if let Err(error) = self.window.show_topmost() {
            self.hide();
            return Err(error);
        }
        self.visible = true;
        self.bounds = Some(bounds);
        if delta != 0 && previous_bounds.is_some_and(|previous| previous.left != bounds.left) {
            self.pending_drag_position = Some((bounds.left, bounds.top));
        }
        if drag_finished {
            self.position_change = self.pending_drag_position.take();
        }
        Ok(())
    }

    pub fn hide(&mut self) {
        self.window.hide();
        self.window.take_drag_delta();
        self.window.take_drag_finished();
        self.pending_drag_position = None;
        self.visible = false;
        self.bounds = None;
    }
}
