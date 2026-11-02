//! Five-card physical display and transparent desktop overlay rasterization.
use crate::{
    dashboard_layout::{layout, vertical_gauge, CardKind, Orientation},
    dirty::Rect,
    native_text::TextRaster,
    taskbar_render::TaskbarRenderer,
};
use std::{
    collections::{HashSet, VecDeque},
    io,
};

// No Debug: values and account details are private.
#[derive(Clone, PartialEq)]
pub struct DashboardEntry {
    pub kind: CardKind,
    pub value: String,
    pub details: Vec<String>,
    pub gauge: Option<f64>,
}
#[derive(Clone, PartialEq)]
pub struct DashboardInput {
    pub port: String,
    pub entries: Vec<DashboardEntry>,
}
pub struct DashboardFrame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
    pub detail_pages: Vec<(CardKind, usize)>,
}
impl DashboardFrame {
    /// Physical display conversion is allowed only for a fully opaque frame.
    pub fn to_opaque_rgb(&self) -> io::Result<Vec<u8>> {
        let expected = (self.width as usize)
            .checked_mul(self.height as usize)
            .and_then(|n| n.checked_mul(4));
        if self.width == 0
            || self.height == 0
            || expected != Some(self.pixels.len())
            || self.pixels.as_chunks::<4>().0.iter().any(|p| p[3] != 255)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "frame is not opaque",
            ));
        }
        let mut rgb = Vec::with_capacity(self.width as usize * self.height as usize * 3);
        for pixel in self.pixels.as_chunks::<4>().0 {
            rgb.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]);
        }
        Ok(rgb)
    }
}

pub struct DashboardRenderer {
    text: TaskbarRenderer,
    detail_page: usize,
    wraps: VecDeque<(String, u32, Vec<String>)>,
}
impl Default for DashboardRenderer {
    fn default() -> Self {
        Self::new()
    }
}
impl DashboardRenderer {
    pub fn new() -> Self {
        Self {
            text: TaskbarRenderer::new(),
            detail_page: 0,
            wraps: VecDeque::new(),
        }
    }

    /// The caller advances this at a low rate to reveal long model names.
    pub fn set_detail_page(&mut self, page: usize) {
        self.detail_page = page;
    }

    fn wrapped(&mut self, value: &str, width: u32) -> io::Result<Vec<String>> {
        if let Some((_, _, lines)) = self
            .wraps
            .iter()
            .find(|(text, w, _)| text == value && *w == width)
        {
            return Ok(lines.clone());
        }
        let lines = wrap(&mut self.text, value, width)?;
        if self.wraps.len() >= 32 {
            self.wraps.pop_front();
        }
        self.wraps
            .push_back((value.to_owned(), width, lines.clone()));
        Ok(lines)
    }

    /// None = opaque physical display; Some(alpha) = overlay card background opacity.
    pub fn render(
        &mut self,
        input: &DashboardInput,
        orientation: Orientation,
        with_api: bool,
        overlay: Option<u8>,
    ) -> io::Result<DashboardFrame> {
        if input.port.len() > 128
            || input.port.chars().any(char::is_control)
            || input.entries.len() > 6
            || input.entries.iter().any(|e| {
                e.kind == CardKind::Header
                    || e.value.is_empty()
                    || e.value.len() > 128
                    || e.value.chars().any(char::is_control)
                    || e.details.len() > 4
                    || e.details
                        .iter()
                        .any(|d| d.len() > 128 || d.chars().any(char::is_control))
            })
            || input
                .entries
                .iter()
                .map(|entry| entry.kind)
                .collect::<HashSet<_>>()
                .len()
                != input.entries.len()
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid dashboard data",
            ));
        }
        let layout = layout(orientation, with_api);
        let mut frame = DashboardFrame {
            width: layout.width,
            height: layout.height,
            pixels: vec![0; (layout.width * layout.height * 4) as usize],
            detail_pages: Vec::new(),
        };
        if overlay.is_none() {
            fill(
                &mut frame,
                Rect {
                    x: 0,
                    y: 0,
                    width: layout.width,
                    height: layout.height,
                },
                [7, 8, 10],
                255,
            );
        }
        let opacity = overlay.unwrap_or(255);
        for card in &layout.cards {
            fill(
                &mut frame,
                card.rect,
                if card.kind == CardKind::Header {
                    [28, 31, 36]
                } else {
                    [24, 27, 32]
                },
                opacity,
            );
            if card.kind == CardKind::Header {
                let title = self.text.glyph("MINI MONITOR", 10, [235, 239, 244])?;
                text(
                    &mut frame,
                    card.rect,
                    card.rect.x + 8,
                    card.rect.y + 4,
                    &title,
                );
                if !input.port.is_empty() {
                    let port = self.text.glyph(&input.port, 9, [175, 185, 198])?;
                    let x = (card.rect.x + card.rect.width)
                        .saturating_sub(port.width + 8)
                        .max(card.rect.x);
                    text(&mut frame, card.rect, x, card.rect.y + 5, &port);
                }
                continue;
            }
            let title = match card.kind {
                CardKind::Cpu => "CPU",
                CardKind::Ram => "RAM",
                CardKind::Gpu => "GPU",
                CardKind::Vram => "VRAM",
                CardKind::Codex => "CODEX",
                CardKind::Api => "OPENAI API",
                CardKind::Header => unreachable!(),
            };
            let color = match card.kind {
                CardKind::Cpu => [58, 226, 220],
                CardKind::Ram => [54, 217, 151],
                CardKind::Gpu => [160, 125, 255],
                CardKind::Vram => [143, 117, 247],
                CardKind::Codex => [83, 163, 255],
                CardKind::Api => [255, 190, 94],
                CardKind::Header => unreachable!(),
            };
            let title_glyph = self.text.glyph(title, 16, [240, 243, 248])?;
            text(
                &mut frame,
                card.rect,
                card.rect.x + 9,
                card.rect.y + 5,
                &title_glyph,
            );
            let entry = input.entries.iter().find(|entry| entry.kind == card.kind);
            let value = entry.map_or("--", |entry| entry.value.as_str());
            let track = Rect {
                x: card.rect.x + card.rect.width - 13,
                y: card.rect.y + 27,
                width: 6,
                height: card.rect.height - 35,
            };
            fill(&mut frame, track, [64, 70, 80], opacity);
            if let Some(fill_rect) = vertical_gauge(track, entry.and_then(|entry| entry.gauge)) {
                fill(&mut frame, fill_rect, color, 255);
            }
            let value_right = track.x.saturating_sub(5);
            let mut font = if value.ends_with('%') { 38 } else { 23 };
            let (main, suffix) = if let Some(numeric) = value.strip_suffix('%') {
                (numeric, Some("%"))
            } else {
                (value, None)
            };
            let (number, percent) = loop {
                let number = self.text.glyph(main, font, [244, 247, 252])?;
                let percent = suffix
                    .map(|s| self.text.glyph(s, (font / 2).max(8), [244, 247, 252]))
                    .transpose()?;
                let total = number.width + percent.as_ref().map_or(0, |p| p.width);
                if total <= card.rect.width - 28 || font <= 10 {
                    break (number, percent);
                }
                font -= 1;
            };
            let total = number.width + percent.as_ref().map_or(0, |p| p.width);
            let value_x = value_right.saturating_sub(total).max(card.rect.x + 6);
            let value_bottom = card.rect.y + card.rect.height - 6;
            text(
                &mut frame,
                card.rect,
                value_x,
                value_bottom.saturating_sub(number.ink_bottom()),
                &number,
            );
            if let Some(percent) = percent {
                text(
                    &mut frame,
                    card.rect,
                    value_x + number.width,
                    value_bottom.saturating_sub(percent.ink_bottom()),
                    &percent,
                );
            }
            if let Some(entry) = entry {
                let left = card.rect.x + 9;
                let max_width = value_x.saturating_sub(left + 5);
                if max_width >= 20 {
                    let mut all_lines = Vec::new();
                    for detail in &entry.details {
                        all_lines.extend(self.wrapped(detail, max_width)?);
                    }
                    let pages = all_lines.len().div_ceil(3);
                    if pages > 0 {
                        frame.detail_pages.push((card.kind, pages));
                        let start = (self.detail_page % pages) * 3;
                        for (line, part) in all_lines.iter().skip(start).take(3).enumerate() {
                            let glyph = self.text.glyph(part, 10, [166, 178, 192])?;
                            text(
                                &mut frame,
                                card.rect,
                                left,
                                card.rect.y + 27 + line as u32 * 12,
                                &glyph,
                            );
                        }
                    }
                }
            }
        }
        Ok(frame)
    }
}

fn wrap(cache: &mut TaskbarRenderer, text_value: &str, max_width: u32) -> io::Result<Vec<String>> {
    let mut result = Vec::new();
    let mut line = String::new();
    for word in text_value.split_whitespace() {
        let candidate = if line.is_empty() {
            word.to_owned()
        } else {
            format!("{line} {word}")
        };
        if cache.glyph(&candidate, 10, [166, 178, 192])?.width <= max_width {
            line = candidate;
            continue;
        }
        if !line.is_empty() {
            result.push(std::mem::take(&mut line));
        }
        for character in word.chars() {
            let mut candidate = line.clone();
            candidate.push(character);
            if cache.glyph(&candidate, 10, [166, 178, 192])?.width > max_width && !line.is_empty() {
                result.push(std::mem::take(&mut line));
            }
            line.push(character);
        }
    }
    if !line.is_empty() {
        result.push(line);
    }
    Ok(result)
}

fn fill(frame: &mut DashboardFrame, rect: Rect, color: [u8; 3], alpha: u8) {
    for y in rect.y..(rect.y + rect.height).min(frame.height) {
        for x in rect.x..(rect.x + rect.width).min(frame.width) {
            let offset = ((y * frame.width + x) * 4) as usize;
            let premul = |c: u8| ((u16::from(c) * u16::from(alpha) + 127) / 255) as u8;
            blend(
                &mut frame.pixels[offset..offset + 4],
                [premul(color[2]), premul(color[1]), premul(color[0]), alpha],
            );
        }
    }
}
fn blend(dst: &mut [u8], src: [u8; 4]) {
    let alpha = u16::from(src[3]);
    for i in 0..4 {
        dst[i] =
            (u16::from(src[i]) + (u16::from(dst[i]) * (255 - alpha) + 127) / 255).min(255) as u8;
    }
}
fn text(frame: &mut DashboardFrame, clip: Rect, x: u32, y: u32, glyph: &TextRaster) {
    for row in 0..glyph.height {
        for col in 0..glyph.width {
            let Some(px) = x.checked_add(col) else {
                continue;
            };
            let Some(py) = y.checked_add(row) else {
                continue;
            };
            if px < clip.x
                || py < clip.y
                || px >= clip.x + clip.width
                || py >= clip.y + clip.height
                || px >= frame.width
                || py >= frame.height
            {
                continue;
            }
            let source = ((row * glyph.width + col) * 4) as usize;
            let dest = ((py * frame.width + px) * 4) as usize;
            blend(
                &mut frame.pixels[dest..dest + 4],
                glyph.pixels[source..source + 4]
                    .try_into()
                    .expect("four bytes"),
            );
        }
    }
}
