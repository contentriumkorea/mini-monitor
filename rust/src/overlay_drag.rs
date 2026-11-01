//! Bounded coalescing for a desktop overlay drag; no UI callbacks or persistence here.
#[derive(Default)]
pub struct OverlayDrag {
    anchor: Option<(i32, i32)>,
    pending: (i32, i32),
    finished: bool,
}
impl OverlayDrag {
    pub fn begin(&mut self, x: i32, y: i32) {
        self.anchor = Some((x, y));
        self.finished = false;
    }
    pub fn motion(&mut self, x: i32, y: i32, pressed: bool, captured: bool) {
        if !pressed || !captured {
            self.end();
            return;
        }
        if let Some((old_x, old_y)) = self.anchor {
            self.pending.0 = self.pending.0.saturating_add(x.saturating_sub(old_x));
            self.pending.1 = self.pending.1.saturating_add(y.saturating_sub(old_y));
            self.anchor = Some((x, y));
        }
    }
    pub fn end(&mut self) {
        if self.anchor.take().is_some() {
            self.finished = true;
        }
    }
    pub fn take_delta(&mut self) -> (i32, i32) {
        std::mem::take(&mut self.pending)
    }
    pub fn take_finished(&mut self) -> bool {
        std::mem::take(&mut self.finished)
    }
}
