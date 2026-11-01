//! Pure geometry in one consistent physical-pixel DPI context.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

impl Rect {
    fn width(self) -> i64 {
        i64::from(self.right) - i64::from(self.left)
    }
    fn height(self) -> i64 {
        i64::from(self.bottom) - i64::from(self.top)
    }
    fn valid(self) -> bool {
        self.width() > 0 && self.height() > 0
    }
}

/// Hide when Explorer leaves only the auto-hide strip inside the monitor.
pub fn taskbar_visible(bar: Rect, monitor: Rect) -> bool {
    if !bar.valid() || !monitor.valid() {
        return false;
    }
    let width = i64::from(bar.right.min(monitor.right)) - i64::from(bar.left.max(monitor.left));
    let height = i64::from(bar.bottom.min(monitor.bottom)) - i64::from(bar.top.max(monitor.top));
    let minimum = 4.max(bar.width().min(bar.height()) / 2);
    width >= minimum && height >= minimum
}

/// Fullscreen on a different monitor must not suppress this monitor's bar.
pub fn covers_monitor(client: Rect, monitor: Rect) -> bool {
    client.valid()
        && monitor.valid()
        && client.left <= monitor.left
        && client.top <= monitor.top
        && client.right >= monitor.right
        && client.bottom >= monitor.bottom
}

/// Clamp a horizontal readout away from the notification area; do not guess for vertical bars.
pub fn place_horizontal(
    bar: Rect,
    notification: Option<Rect>,
    width: i32,
    desired_x: i32,
    margin: i32,
) -> Option<Rect> {
    if !bar.valid() || bar.width() <= bar.height() || width <= 0 || margin < 0 {
        return None;
    }
    let (left, right, padding, required) = (
        i64::from(bar.left),
        i64::from(bar.right),
        i64::from(margin),
        i64::from(width),
    );
    let tray = notification.filter(|tray| {
        tray.valid()
            && tray.top < bar.bottom
            && tray.bottom > bar.top
            && tray.left < bar.right
            && tray.right > bar.left
    })?;
    let tray_left = i64::from(tray.left).max(left);
    let tray_right = i64::from(tray.right).min(right);
    let spans = [
        (left + padding, tray_left - padding),
        (tray_right + padding, right - padding),
    ];
    let desired = i64::from(desired_x);
    let x = spans
        .into_iter()
        .filter_map(|(start, end)| {
            if end - start < required {
                None
            } else {
                Some(desired.clamp(start, end - required))
            }
        })
        .min_by_key(|candidate| (candidate - desired).abs())?;
    Some(Rect {
        left: i32::try_from(x).ok()?,
        top: bar.top,
        right: i32::try_from(x + required).ok()?,
        bottom: bar.bottom,
    })
}
