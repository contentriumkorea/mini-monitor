//! Transparent, independently controlled overlay; caller owns the UI message loop.
use crate::{
    dashboard_layout::Orientation,
    dashboard_render::{DashboardInput, DashboardRenderer},
    overlay_scale::scale_premultiplied_bgra,
    overlay_window::OverlayWindow,
};
use std::io;

struct FrameKey {
    input: DashboardInput,
    orientation: Orientation,
    with_api: bool,
    detail_page: usize,
    scale: u32,
    opacity: u8,
}
pub struct OverlayController {
    window: OverlayWindow,
    renderer: DashboardRenderer,
    x: i32,
    y: i32,
    scale: u32,
    opacity: u8,
    bounds: Option<(i32, i32, u32, u32)>,
    position_change: Option<(i32, i32)>,
    key: Option<FrameKey>,
}
impl OverlayController {
    pub fn new(x: i32, y: i32, scale: u32, opacity: u8) -> io::Result<Self> {
        if !(50..=200).contains(&scale) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid overlay scale",
            ));
        }
        Ok(Self {
            window: OverlayWindow::new_hidden()?,
            renderer: DashboardRenderer::new(),
            x,
            y,
            scale,
            opacity,
            bounds: None,
            position_change: None,
            key: None,
        })
    }
    pub fn set_options(&mut self, scale: u32, opacity: u8) -> io::Result<()> {
        if !(50..=200).contains(&scale) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid overlay scale",
            ));
        }
        self.scale = scale;
        self.opacity = opacity;
        Ok(())
    }
    pub fn bounds(&self) -> Option<(i32, i32, u32, u32)> {
        self.bounds
    }
    pub fn take_position_change(&mut self) -> Option<(i32, i32)> {
        self.position_change.take()
    }
    pub fn render(
        &mut self,
        input: &DashboardInput,
        orientation: Orientation,
        with_api: bool,
        detail_page: usize,
    ) -> io::Result<bool> {
        let (dx, dy) = self.window.take_drag_delta();
        self.x = self.x.saturating_add(dx);
        self.y = self.y.saturating_add(dy);
        if self.window.take_drag_finished() {
            self.position_change = Some((self.x, self.y));
        }
        if dx == 0
            && dy == 0
            && self.key.as_ref().is_some_and(|key| {
                key.input == *input
                    && key.orientation == orientation
                    && key.with_api == with_api
                    && key.detail_page == detail_page
                    && key.scale == self.scale
                    && key.opacity == self.opacity
            })
        {
            return Ok(false);
        }
        self.renderer.set_detail_page(detail_page);
        let frame = self
            .renderer
            .render(input, orientation, with_api, Some(self.opacity))?;
        let (width, height, pixels) =
            scale_premultiplied_bgra(frame.width, frame.height, &frame.pixels, self.scale)?;
        self.x
            .checked_add(width as i32)
            .and_then(|_| self.y.checked_add(height as i32))
            .ok_or_else(|| io::Error::other("overlay position overflow"))?;
        self.window
            .present(self.x, self.y, width, height, &pixels)?;
        self.bounds = Some((self.x, self.y, width, height));
        self.key = Some(FrameKey {
            input: input.clone(),
            orientation,
            with_api,
            detail_page,
            scale: self.scale,
            opacity: self.opacity,
        });
        Ok(true)
    }
    pub fn show(&self) -> io::Result<()> {
        if self.bounds.is_none() {
            return Err(io::Error::other("overlay has no frame"));
        }
        self.window.show_topmost()
    }
    pub fn hide(&self) {
        self.window.hide();
    }
}
