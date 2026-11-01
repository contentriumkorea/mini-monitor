//! Bounded native dirty-region detection. This module performs no device I/O.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Rect {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

#[derive(Debug, Eq, PartialEq)]
pub enum Dirty {
    Full,
    Regions(Vec<Rect>),
}

impl Rect {
    fn right(self) -> u32 {
        self.x + self.width
    }
    fn bottom(self) -> u32 {
        self.y + self.height
    }
    fn area(self) -> u64 {
        u64::from(self.width) * u64::from(self.height)
    }
    fn union(self, other: Self) -> Self {
        let x = self.x.min(other.x);
        let y = self.y.min(other.y);
        Self {
            x,
            y,
            width: self.right().max(other.right()) - x,
            height: self.bottom().max(other.bottom()) - y,
        }
    }
    fn mergeable(self, other: Self, proximity: u32) -> bool {
        let dx = self
            .x
            .max(other.x)
            .saturating_sub(self.right().min(other.right()));
        let dy = self
            .y
            .max(other.y)
            .saturating_sub(self.bottom().min(other.bottom()));
        if dx > proximity || dy > proximity {
            return false;
        }
        let overlap = u64::from(
            self.right()
                .min(other.right())
                .saturating_sub(self.x.max(other.x)),
        ) * u64::from(
            self.bottom()
                .min(other.bottom())
                .saturating_sub(self.y.max(other.y)),
        );
        let useful = self.area() + other.area() - overlap;
        useful > 0 && self.union(other).area() * 100 <= useful * 135
    }
}

/// Frames are packed RGB888. All rectangles are constrained to their own card.
pub fn calculate(
    width: u32,
    height: u32,
    previous: Option<&[u8]>,
    current: &[u8],
    cards: &[Rect],
    padding: u32,
    proximity: u32,
) -> Result<Dirty, &'static str> {
    if !(1..=1024).contains(&width)
        || !(1..=1024).contains(&height)
        || cards.is_empty()
        || cards.len() > 16
        || padding > 1024
        || proximity > 1024
    {
        return Err("invalid dimensions");
    }
    let expected = width as usize * height as usize * 3;
    if current.len() != expected || previous.is_some_and(|value| value.len() != expected) {
        return Err("invalid frame");
    }
    if cards.iter().any(|card| {
        card.width == 0
            || card.height == 0
            || card
                .x
                .checked_add(card.width)
                .is_none_or(|right| right > width)
            || card
                .y
                .checked_add(card.height)
                .is_none_or(|bottom| bottom > height)
    }) {
        return Err("invalid card");
    }
    for (index, card) in cards.iter().enumerate() {
        if cards[index + 1..].iter().any(|other| {
            card.x < other.right()
                && other.x < card.right()
                && card.y < other.bottom()
                && other.y < card.bottom()
        }) {
            return Err("overlapping cards");
        }
    }
    let Some(previous) = previous else {
        return Ok(Dirty::Full);
    };
    let mut output = Vec::new();
    for card in cards.iter().copied() {
        let card_width = card.width as usize;
        let card_height = card.height as usize;
        let mut visited = vec![false; card_width * card_height];
        let changed = |x: usize, y: usize| {
            let index = ((card.y as usize + y) * width as usize + card.x as usize + x) * 3;
            previous[index..index + 3] != current[index..index + 3]
        };
        let mut components: Vec<Rect> = Vec::new();
        for y in 0..card_height {
            for x in 0..card_width {
                let index = y * card_width + x;
                if visited[index] || !changed(x, y) {
                    continue;
                }
                visited[index] = true;
                let mut stack = vec![(x, y)];
                let (mut left, mut right, mut top, mut bottom) = (x, x, y, y);
                while let Some((px, py)) = stack.pop() {
                    left = left.min(px);
                    right = right.max(px);
                    top = top.min(py);
                    bottom = bottom.max(py);
                    for ny in py.saturating_sub(1)..=(py + 1).min(card_height - 1) {
                        for nx in px.saturating_sub(1)..=(px + 1).min(card_width - 1) {
                            let neighbor = ny * card_width + nx;
                            if !visited[neighbor] && changed(nx, ny) {
                                visited[neighbor] = true;
                                stack.push((nx, ny));
                            }
                        }
                    }
                }
                let x0 = (card.x + left as u32).saturating_sub(padding).max(card.x);
                let y0 = (card.y + top as u32).saturating_sub(padding).max(card.y);
                let x1 = (card.x + right as u32 + 1)
                    .saturating_add(padding)
                    .min(card.right());
                let y1 = (card.y + bottom as u32 + 1)
                    .saturating_add(padding)
                    .min(card.bottom());
                components.push(Rect {
                    x: x0,
                    y: y0,
                    width: x1 - x0,
                    height: y1 - y0,
                });
                if components.len() + output.len() > 128 {
                    return Ok(Dirty::Full);
                }
            }
        }
        loop {
            let mut merged = false;
            'outer: for i in 0..components.len() {
                for j in i + 1..components.len() {
                    if components[i].mergeable(components[j], proximity) {
                        components[i] = components[i].union(components[j]);
                        components.remove(j);
                        merged = true;
                        break 'outer;
                    }
                }
            }
            if !merged {
                break;
            }
        }
        output.extend(components);
    }
    output.sort_by_key(|rect| (rect.y, rect.x));
    Ok(Dirty::Regions(output))
}
