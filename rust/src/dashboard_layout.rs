//! Logical physical-display card layout in either orientation.
use crate::dirty::Rect;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Orientation {
    Landscape,
    Portrait,
}
#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum CardKind {
    Header,
    Cpu,
    Ram,
    Gpu,
    Vram,
    Codex,
    Api,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Card {
    pub kind: CardKind,
    pub rect: Rect,
}
pub struct DashboardLayout {
    pub width: u32,
    pub height: u32,
    pub cards: Vec<Card>,
}

fn card(kind: CardKind, x: u32, y: u32, width: u32, height: u32) -> Card {
    Card {
        kind,
        rect: Rect {
            x,
            y,
            width,
            height,
        },
    }
}

pub fn layout(orientation: Orientation, with_api: bool) -> DashboardLayout {
    use CardKind::*;
    let (width, height, mut cards) = match orientation {
        Orientation::Landscape => (
            480,
            320,
            vec![
                card(Header, 8, 8, 464, 20),
                card(Cpu, 8, 36, 228, 88),
                card(Ram, 244, 36, 228, 88),
                card(Gpu, 8, 132, 228, 88),
                card(Vram, 244, 132, 228, 88),
                card(Codex, 8, 228, 464, 84),
            ],
        ),
        Orientation::Portrait => (
            320,
            480,
            vec![
                card(Header, 8, 8, 304, 20),
                card(Cpu, 8, 36, 304, 81),
                card(Ram, 8, 125, 304, 81),
                card(Gpu, 8, 214, 304, 81),
                card(Vram, 8, 303, 304, 81),
                card(Codex, 8, 392, 304, 80),
            ],
        ),
    };
    if with_api {
        let codex = cards.last_mut().expect("static layout includes Codex");
        let available = codex.rect.width;
        let left_width = (available - 8) / 2;
        codex.rect.width = left_width;
        let right = card(
            Api,
            codex.rect.x + left_width + 8,
            codex.rect.y,
            available - left_width - 8,
            codex.rect.height,
        );
        cards.push(right);
    }
    DashboardLayout {
        width,
        height,
        cards,
    }
}

/// Returns no fill for unavailable data, zero, or invalid geometry.
pub fn vertical_gauge(track: Rect, percent: Option<f64>) -> Option<Rect> {
    let percent = percent.filter(|v| v.is_finite() && *v >= 0.0)?.min(100.0);
    if track.width == 0 || track.height == 0 {
        return None;
    }
    let bottom = track.y.checked_add(track.height)?;
    let filled = (f64::from(track.height) * percent / 100.0).round_ties_even() as u32;
    if filled == 0 {
        None
    } else {
        Some(Rect {
            x: track.x,
            y: bottom - filled,
            width: track.width,
            height: filled,
        })
    }
}
