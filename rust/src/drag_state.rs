//! Minimal bounded state for horizontal taskbar dragging.
#[derive(Default)]
pub struct DragState {
    previous: Option<i32>,
    delta: i64,
    finished: bool,
}
impl DragState {
    pub fn begin(&mut self, screen_x: i32) {
        self.previous = Some(screen_x);
        self.finished = false;
    }
    pub fn end(&mut self) {
        if self.previous.is_some() {
            self.finished = true;
        }
        self.previous = None;
    }
    pub fn motion(&mut self, screen_x: i32, button_down: bool, captured: bool) {
        if !button_down || !captured {
            self.end();
            return;
        }
        if let Some(previous) = self.previous {
            self.delta = self
                .delta
                .saturating_add(i64::from(screen_x) - i64::from(previous));
            self.previous = Some(screen_x);
        }
    }
    pub fn take_delta(&mut self) -> i32 {
        let delta = std::mem::take(&mut self.delta);
        delta.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32
    }
    pub fn take_finished(&mut self) -> bool {
        std::mem::take(&mut self.finished)
    }
}
