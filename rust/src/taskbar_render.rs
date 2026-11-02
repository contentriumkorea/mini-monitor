//! Transparent taskbar readouts with bounded glyph and icon caches.
use crate::{
    native_icon::{load_icon, metric_symbol, IconKind, IconRaster},
    native_text::{raster_text, TextRaster},
};
use std::{
    collections::{HashSet, VecDeque},
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
pub enum MetricKind {
    Cpu,
    Ram,
    Gpu,
    Vram,
    Codex,
    Api,
}
impl MetricKind {
    fn label(self) -> &'static str {
        match self {
            Self::Cpu => "CPU",
            Self::Ram => "RAM",
            Self::Gpu => "GPU",
            Self::Vram => "VRAM",
            Self::Codex => "CODEX",
            Self::Api => "API",
        }
    }
    fn icon(self) -> Option<IconKind> {
        match self {
            Self::Cpu => Some(IconKind::Cpu),
            Self::Ram => Some(IconKind::Ram),
            Self::Gpu => Some(IconKind::Gpu),
            Self::Vram => Some(IconKind::Vram),
            Self::Codex => Some(IconKind::Codex),
            Self::Api => Some(IconKind::Api),
        }
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
pub enum TaskbarStyle {
    Text,
    Icon,
    Both,
}

// No Debug: pixel buffer can contain private account usage.
pub struct TaskbarFrame {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
    pub segments: Vec<Segment>,
}

pub struct Segment {
    pub left: u32,
    pub right: u32,
}

#[derive(Eq, PartialEq)]
struct GlyphKey {
    text: String,
    font: u32,
    color: [u8; 3],
}
pub struct TaskbarRenderer {
    glyphs: VecDeque<(GlyphKey, Arc<TextRaster>)>,
    glyph_bytes: usize,
    icons: VecDeque<(PathBuf, IconKind, u32, Arc<IconRaster>)>,
}

impl Default for TaskbarRenderer {
    fn default() -> Self {
        Self::new()
    }
}
impl TaskbarRenderer {
    pub fn new() -> Self {
        Self {
            glyphs: VecDeque::new(),
            glyph_bytes: 0,
            icons: VecDeque::new(),
        }
    }
    pub fn cached_glyphs(&self) -> usize {
        self.glyphs.len()
    }
    pub fn cached_icons(&self) -> usize {
        self.icons.len()
    }
    pub fn cached_glyph_bytes(&self) -> usize {
        self.glyph_bytes
    }
    pub(crate) fn glyph(
        &mut self,
        text: &str,
        font: u32,
        color: [u8; 3],
    ) -> io::Result<Arc<TextRaster>> {
        let key = GlyphKey {
            text: text.to_owned(),
            font,
            color,
        };
        if let Some((_, value)) = self.glyphs.iter().find(|(candidate, _)| *candidate == key) {
            return Ok(Arc::clone(value));
        }
        let value = Arc::new(raster_text(text, font, color)?);
        let bytes = value.pixels.len();
        const MAX_GLYPH_BYTES: usize = 2 * 1024 * 1024;
        if bytes > MAX_GLYPH_BYTES {
            return Ok(value);
        }
        while self.glyphs.len() >= 64 || self.glyph_bytes + bytes > MAX_GLYPH_BYTES {
            if let Some((_, evicted)) = self.glyphs.pop_front() {
                self.glyph_bytes -= evicted.pixels.len();
            } else {
                break;
            }
        }
        self.glyph_bytes += bytes;
        self.glyphs.push_back((key, Arc::clone(&value)));
        Ok(value)
    }

    fn icon(&mut self, root: &Path, kind: IconKind, side: u32) -> io::Result<Arc<IconRaster>> {
        if let Some((_, _, _, icon)) = self
            .icons
            .iter()
            .find(|(path, k, size, _)| path == root && *k == kind && *size == side)
        {
            return Ok(Arc::clone(icon));
        }
        let icon = Arc::new(load_icon(root, kind, side)?);
        if self.icons.len() >= 24 {
            self.icons.pop_front();
        }
        self.icons
            .push_back((root.to_path_buf(), kind, side, Arc::clone(&icon)));
        Ok(icon)
    }

    fn value_glyph(
        &mut self,
        value: &str,
        font: u32,
        color: [u8; 3],
    ) -> io::Result<Arc<TextRaster>> {
        let Some(digits) = value
            .strip_suffix('%')
            .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
        else {
            return self.glyph(value, font, color);
        };
        let number = self.glyph(digits, font, color)?;
        let percent = self.glyph("%", ((font * 7 + 5) / 10).max(8), color)?;
        let baseline = number.ink_bottom().max(percent.ink_bottom());
        let number_top = baseline - number.ink_bottom();
        let percent_top = baseline - percent.ink_bottom();
        let width = number.width + percent.width;
        let height = (number_top + number.height).max(percent_top + percent.height);
        let mut pixels = vec![0; (width * height * 4) as usize];
        for (glyph, left, top) in [
            (&number, 0, number_top),
            (&percent, number.width, percent_top),
        ] {
            for y in 0..glyph.height {
                let source = (y * glyph.width * 4) as usize;
                let target = (((top + y) * width + left) * 4) as usize;
                pixels[target..target + (glyph.width * 4) as usize]
                    .copy_from_slice(&glyph.pixels[source..source + (glyph.width * 4) as usize]);
            }
        }
        Ok(Arc::new(TextRaster {
            width,
            height,
            pixels,
        }))
    }

    /// Caller chooses the exact items. Pixel dimensions are scaled from 96-DPI logical units.
    pub fn render_text(
        &mut self,
        items: &[(MetricKind, &str)],
        dpi: u32,
    ) -> io::Result<TaskbarFrame> {
        self.render(items, dpi, TaskbarStyle::Text, None)
    }

    pub fn render(
        &mut self,
        items: &[(MetricKind, &str)],
        dpi: u32,
        style: TaskbarStyle,
        root: Option<&Path>,
    ) -> io::Result<TaskbarFrame> {
        if items.is_empty()
            || items.len() > 6
            || !(96..=768).contains(&dpi)
            || items
                .iter()
                .map(|(kind, _)| kind)
                .collect::<HashSet<_>>()
                .len()
                != items.len()
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid taskbar items",
            ));
        }
        let scale = |logical: u32| (logical * dpi + 48) / 96;
        let label_font = scale(10).clamp(8, 96);
        let value_font = scale(14).clamp(8, 96);
        let height = scale(32);
        // Reserve a two-digit percentage cell, regardless of the live value.
        let percentage_width = self.value_glyph("88%", value_font, [248, 248, 248])?.width;
        let mut rows = Vec::new();
        let mut width = scale(4);
        for (kind, value) in items {
            let icon = if style != TaskbarStyle::Text {
                if let Some(kind) = kind.icon() {
                    Some(self.icon(
                        root.ok_or_else(|| io::Error::other("icon assets unavailable"))?,
                        kind,
                        scale(18),
                    )?)
                } else {
                    Some(Arc::new(self.api_icon(scale(18))?))
                }
            } else {
                None
            };
            let label = if style != TaskbarStyle::Icon {
                Some(self.glyph(kind.label(), label_font, [190, 190, 190])?)
            } else {
                None
            };
            let value = self.value_glyph(value, value_font, [248, 248, 248])?;
            let cell_width = value.width.max(percentage_width);
            let prefix = icon.as_ref().map_or(0, |image| image.width + scale(5))
                + label.as_ref().map_or(0, |text| text.width + scale(5));
            width = width
                .checked_add(prefix + cell_width + scale(12))
                .ok_or_else(|| io::Error::other("frame too wide"))?;
            rows.push((icon, label, value, cell_width));
        }
        if width > 8192 || height > 512 {
            return Err(io::Error::other("frame too large"));
        }
        let mut pixels = vec![0; width as usize * height as usize * 4];
        let mut x = scale(4);
        let mut segments = Vec::with_capacity(rows.len());
        for (icon, label, value, cell_width) in rows {
            let left = x;
            if let Some(icon) = icon {
                blit(
                    &mut pixels,
                    width,
                    height,
                    x,
                    icon.width,
                    icon.height,
                    &icon.pixels,
                );
                x += icon.width + scale(5);
            }
            if let Some(label) = label {
                blit(
                    &mut pixels,
                    width,
                    height,
                    x,
                    label.width,
                    label.height,
                    &label.pixels,
                );
                x += label.width + scale(5);
            }
            blit(
                &mut pixels,
                width,
                height,
                x + cell_width - value.width,
                value.width,
                value.height,
                &value.pixels,
            );
            segments.push(Segment {
                left,
                right: x + cell_width,
            });
            x += cell_width + scale(12);
        }
        Ok(TaskbarFrame {
            width,
            height,
            pixels,
            segments,
        })
    }

    fn api_icon(&mut self, side: u32) -> io::Result<IconRaster> {
        metric_symbol(IconKind::Api, side)
    }
}

fn blit(
    frame: &mut [u8],
    width: u32,
    height: u32,
    x: u32,
    src_width: u32,
    src_height: u32,
    source: &[u8],
) {
    let y = height.saturating_sub(src_height) / 2;
    for row in 0..src_height.min(height) {
        for column in 0..src_width.min(width.saturating_sub(x)) {
            let src = ((row * src_width + column) * 4) as usize;
            let dst = (((y + row) * width + x + column) * 4) as usize;
            let alpha = u16::from(source[src + 3]);
            for channel in 0..4 {
                let blended = u16::from(source[src + channel])
                    + (u16::from(frame[dst + channel]) * (255 - alpha) + 127) / 255;
                frame[dst + channel] = blended.min(255) as u8;
            }
        }
    }
}
