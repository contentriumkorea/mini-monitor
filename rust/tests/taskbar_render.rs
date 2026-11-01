#[cfg(windows)]
#[test]
fn one_and_two_digit_percentages_keep_neighbor_positions_and_three_digits_expand() {
    use mini_monitor_native::taskbar_render::{MetricKind as K, TaskbarRenderer, TaskbarStyle};
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let mut renderer = TaskbarRenderer::new();
    for dpi in [96, 144, 192, 240] {
        for style in [TaskbarStyle::Text, TaskbarStyle::Icon, TaskbarStyle::Both] {
            let baseline = renderer
                .render(&[(K::Cpu, "88%"), (K::Gpu, "50%")], dpi, style, Some(&root))
                .unwrap();
            for value in ["0%", "1%", "9%", "10%", "11%", "99%", "--"] {
                let frame = renderer
                    .render(&[(K::Cpu, value), (K::Gpu, "50%")], dpi, style, Some(&root))
                    .unwrap();
                assert_eq!(frame.width, baseline.width, "unexpected width at {value}");
                assert_eq!(
                    frame.segments[1].left, baseline.segments[1].left,
                    "neighbor moved at {value}"
                );
            }
            let larger = renderer
                .render(
                    &[(K::Cpu, "100%"), (K::Gpu, "50%")],
                    dpi,
                    style,
                    Some(&root),
                )
                .unwrap();
            assert!(larger.segments[1].left > baseline.segments[1].left);
        }
    }
}

#[cfg(windows)]
#[test]
fn selected_text_items_render_on_transparent_background_without_overlap() {
    use mini_monitor_native::taskbar_render::{MetricKind, TaskbarRenderer};
    let mut renderer = TaskbarRenderer::new();
    let frame = renderer
        .render_text(
            &[
                (MetricKind::Cpu, "9%"),
                (MetricKind::Codex, "60,518 Credit"),
            ],
            96,
        )
        .unwrap();
    assert!(frame.width > 0 && frame.height > 0);
    assert_eq!(frame.segments.len(), 2);
    assert!(frame.segments[0].right <= frame.segments[1].left);
    assert!(frame.segments[1].right <= frame.width);
    assert!(frame.pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
    assert!(frame.pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 255));
    assert!(frame
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .all(|p| p[..3].iter().all(|c| *c <= p[3])));
    assert!(renderer
        .render_text(&[(MetricKind::Cpu, "9%"), (MetricKind::Cpu, "10%")], 96)
        .is_err());
    assert!(renderer.render_text(&[], 96).is_err());
}

#[cfg(windows)]
#[test]
fn text_frame_scales_and_cache_is_bounded() {
    use mini_monitor_native::taskbar_render::{MetricKind, TaskbarRenderer};
    let mut renderer = TaskbarRenderer::new();
    let small = renderer
        .render_text(&[(MetricKind::Gpu, "100%")], 96)
        .unwrap();
    let large = renderer
        .render_text(&[(MetricKind::Gpu, "100%")], 192)
        .unwrap();
    assert!(large.width > small.width && large.height > small.height);
    for n in 0..150 {
        renderer
            .render_text(&[(MetricKind::Gpu, &format!("{n}%"))], 96)
            .unwrap();
    }
    assert!(renderer.cached_glyphs() <= 64);
    for n in 0..8 {
        let _ = renderer.render_text(
            &[(
                MetricKind::Codex,
                &format!("{n}123456789012345678901234567890 Credit"),
            )],
            768,
        );
    }
    assert!(renderer.cached_glyph_bytes() <= 2 * 1024 * 1024);
}

#[cfg(windows)]
#[test]
fn icon_and_combined_modes_use_bundled_assets_and_present_offscreen() {
    use mini_monitor_native::{
        layered_window::present,
        taskbar_render::{MetricKind as K, TaskbarRenderer, TaskbarStyle},
        taskbar_window::TaskbarWindow,
    };
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let mut renderer = TaskbarRenderer::new();
    let items = [
        (K::Cpu, "9%"),
        (K::Ram, "20%"),
        (K::Gpu, "4%"),
        (K::Vram, "18%"),
        (K::Codex, "60,518 Credit"),
        (K::Api, "--"),
    ];
    let icon = renderer
        .render(&items, 96, TaskbarStyle::Icon, Some(&root))
        .unwrap();
    let both = renderer
        .render(&items, 96, TaskbarStyle::Both, Some(&root))
        .unwrap();
    assert!(both.width > icon.width);
    assert_eq!(icon.segments.len(), 6);
    assert!(icon
        .segments
        .windows(2)
        .all(|pair| pair[0].right <= pair[1].left));
    assert!(icon.pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
    let window = TaskbarWindow::new_hidden().unwrap();
    present(
        &window,
        -30000,
        -30000,
        icon.width,
        icon.height,
        &icon.pixels,
    )
    .unwrap();
    for dpi in (96..=676).step_by(20) {
        renderer
            .render(&[(K::Cpu, "9%")], dpi, TaskbarStyle::Icon, Some(&root))
            .unwrap();
    }
    assert!(renderer.cached_icons() <= 24);
}
