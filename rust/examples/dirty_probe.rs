//! Isolated synthetic dirty-region benchmark. Never reads live screens or user settings.
use mini_monitor_native::dirty::{calculate, Dirty, Rect};
use std::{path::PathBuf, time::Instant};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let dir = PathBuf::from(
        std::env::args_os()
            .nth(1)
            .ok_or("missing fixture directory")?,
    );
    let before = std::fs::read(dir.join("dirty_before.rgb"))?;
    let after = std::fs::read(dir.join("dirty_after.rgb"))?;
    let rows: Vec<[u32; 4]> =
        serde_json::from_slice(&std::fs::read(dir.join("dirty_cards.json"))?)?;
    let cards: Vec<_> = rows
        .into_iter()
        .map(|[x, y, width, height]| Rect {
            x,
            y,
            width,
            height,
        })
        .collect();
    let started = Instant::now();
    let mut result = Dirty::Full;
    for _ in 0..100 {
        result = calculate(480, 320, Some(&before), &after, &cards, 1, 3)?;
    }
    let elapsed = started.elapsed();
    if let Dirty::Regions(regions) = &result {
        for card in &cards {
            for y in card.y..card.y + card.height {
                for x in card.x..card.x + card.width {
                    let index = (y as usize * 480 + x as usize) * 3;
                    if before[index..index + 3] != after[index..index + 3]
                        && !regions.iter().any(|r| {
                            x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height
                        })
                    {
                        return Err("changed pixel not covered".into());
                    }
                }
            }
        }
    }
    let region_count = match result {
        Dirty::Full => 1,
        Dirty::Regions(regions) => regions.len(),
    };
    println!(
        "iterations=100 elapsed_ms={} regions={region_count} changed_pixels_covered=true",
        elapsed.as_millis()
    );
    Ok(())
}
