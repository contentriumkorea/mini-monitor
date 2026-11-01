//! Bounded scaling for already-premultiplied overlay frames.
use std::io;
pub fn scale_premultiplied_bgra(
    width: u32,
    height: u32,
    pixels: &[u8],
    percent: u32,
) -> io::Result<(u32, u32, Vec<u8>)> {
    let invalid = || {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid overlay frame or scale",
        )
    };
    if !(50..=200).contains(&percent) || width == 0 || height == 0 {
        return Err(invalid());
    }
    let input = (width as usize)
        .checked_mul(height as usize)
        .and_then(|v| v.checked_mul(4))
        .ok_or_else(invalid)?;
    if input != pixels.len()
        || pixels
            .as_chunks::<4>()
            .0
            .iter()
            .any(|p| p[..3].iter().any(|c| *c > p[3]))
    {
        return Err(invalid());
    }
    let output_width = width
        .checked_mul(percent)
        .ok_or_else(invalid)?
        .checked_add(50)
        .ok_or_else(invalid)?
        / 100;
    let output_height = height
        .checked_mul(percent)
        .ok_or_else(invalid)?
        .checked_add(50)
        .ok_or_else(invalid)?
        / 100;
    let count = (output_width as usize)
        .checked_mul(output_height as usize)
        .filter(|v| *v > 0 && *v <= 16_777_216)
        .ok_or_else(invalid)?;
    let mut output = vec![0; count * 4];
    for y in 0..output_height {
        for x in 0..output_width {
            let source_y = (u64::from(y) * u64::from(height) / u64::from(output_height)) as usize;
            let source_x = (u64::from(x) * u64::from(width) / u64::from(output_width)) as usize;
            let source = (source_y * width as usize + source_x) * 4;
            let target = (y as usize * output_width as usize + x as usize) * 4;
            output[target..target + 4].copy_from_slice(&pixels[source..source + 4]);
        }
    }
    Ok((output_width, output_height, output))
}
