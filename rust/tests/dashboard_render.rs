#[cfg(windows)]
#[test]
fn dashboard_draws_both_orientations_and_overlay_transparency() {
    use mini_monitor_native::dashboard_layout::{CardKind, Orientation};
    use mini_monitor_native::dashboard_render::{
        DashboardEntry, DashboardInput, DashboardRenderer,
    };
    let input = DashboardInput {
        port: "COM3".into(),
        entries: vec![
            DashboardEntry {
                kind: CardKind::Cpu,
                value: "16%".into(),
                details: vec!["Intel Core i7 14700K".into()],
                gauge: Some(16.0),
            },
            DashboardEntry {
                kind: CardKind::Gpu,
                value: "2%".into(),
                details: vec!["NVIDIA GeForce RTX 5070 Ti".into(), "42 C".into()],
                gauge: Some(2.0),
            },
            DashboardEntry {
                kind: CardKind::Codex,
                value: "60,518 Credit".into(),
                details: vec!["7D LEFT".into()],
                gauge: Some(0.0),
            },
        ],
    };
    let mut renderer = DashboardRenderer::new();
    let landscape = renderer
        .render(&input, Orientation::Landscape, false, None)
        .unwrap();
    assert_eq!((landscape.width, landscape.height), (480, 320));
    assert!(landscape
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .all(|p| p[3] == 255));
    let rgb = landscape.to_opaque_rgb().unwrap();
    assert_eq!(rgb.len(), 480 * 320 * 3);
    assert_eq!(
        mini_monitor_native::device::rgb888_to_rgb565le(&rgb, 480, 320)
            .unwrap()
            .len(),
        480 * 320 * 2
    );
    let portrait = renderer
        .render(&input, Orientation::Portrait, true, Some(0))
        .unwrap();
    assert_eq!((portrait.width, portrait.height), (320, 480));
    assert_eq!(portrait.pixels[3], 0);
    assert!(portrait.to_opaque_rgb().is_err());
    assert!(portrait
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .any(|p| p[3] == 255));
    assert!(portrait
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .all(|p| p[..3].iter().all(|c| *c <= p[3])));
}

#[cfg(windows)]
#[test]
fn dashboard_rejects_duplicate_or_unbounded_input() {
    use mini_monitor_native::dashboard_layout::{CardKind, Orientation};
    use mini_monitor_native::dashboard_render::DashboardFrame;
    use mini_monitor_native::dashboard_render::{
        DashboardEntry, DashboardInput, DashboardRenderer,
    };
    let item = || DashboardEntry {
        kind: CardKind::Cpu,
        value: "1%".into(),
        details: vec![],
        gauge: Some(1.0),
    };
    let mut renderer = DashboardRenderer::new();
    assert!(renderer
        .render(
            &DashboardInput {
                port: "".into(),
                entries: vec![item(), item()]
            },
            Orientation::Landscape,
            false,
            None
        )
        .is_err());
    assert!(renderer
        .render(
            &DashboardInput {
                port: "x".repeat(300),
                entries: vec![]
            },
            Orientation::Landscape,
            false,
            None
        )
        .is_err());
    assert!(DashboardFrame {
        width: 0,
        height: 0,
        pixels: vec![],
        detail_pages: vec![]
    }
    .to_opaque_rgb()
    .is_err());
}

#[cfg(windows)]
#[test]
fn percent_ink_bottom_matches_large_digits_in_both_orientations() {
    use mini_monitor_native::{
        dashboard_layout::{layout, CardKind, Orientation},
        dashboard_render::*,
    };
    let input = DashboardInput {
        port: String::new(),
        entries: vec![DashboardEntry {
            kind: CardKind::Cpu,
            value: "81%".into(),
            details: vec![],
            gauge: None,
        }],
    };
    for orientation in [Orientation::Landscape, Orientation::Portrait] {
        let frame = DashboardRenderer::new()
            .render(&input, orientation, false, None)
            .unwrap();
        let card = layout(orientation, false)
            .cards
            .into_iter()
            .find(|c| c.kind == CardKind::Cpu)
            .unwrap()
            .rect;
        let end = card.x + card.width - 18;
        let percent_width = mini_monitor_native::native_text::raster_text("%", 19, [244, 247, 252])
            .unwrap()
            .width;
        let split = end - percent_width;
        let bottom = |left, right| {
            (card.y + 35..card.y + card.height)
                .rev()
                .find(|y| {
                    (left..right).any(|x| {
                        let i = ((y * frame.width + x) * 4) as usize;
                        frame.pixels[i] > 140
                            && frame.pixels[i + 1] > 140
                            && frame.pixels[i + 2] > 140
                    })
                })
                .unwrap()
        };
        assert_eq!(
            bottom(card.x + card.width / 2, split),
            bottom(split, end),
            "digits and percent need a common visible bottom"
        );
    }
}

#[cfg(windows)]
#[test]
fn hardware_models_stay_on_one_line_without_paging_or_wrapping() {
    use mini_monitor_native::dashboard_layout::{CardKind, Orientation};
    use mini_monitor_native::dashboard_render::{
        DashboardEntry, DashboardInput, DashboardRenderer,
    };
    for orientation in [Orientation::Landscape, Orientation::Portrait] {
        for (kind, model) in [
            (CardKind::Gpu, "NVIDIA GeForce RTX 5070 Ti"),
            (CardKind::Gpu, "Example NVIDIA GeForce RTX Ultra Long Laptop Workstation Graphics Adapter Name 12345"),
            (CardKind::Cpu, "Intel(R) Core(TM) i7-14700K"),
        ] {
            let input = DashboardInput {port:"".into(), entries:vec![DashboardEntry {
                kind, value:"100%".into(), details:vec![model.into()], gauge:None,
            }]};
            let mut renderer = DashboardRenderer::new();
            let first = renderer.render(&input, orientation, true, None).unwrap();
            assert_eq!(first.detail_pages.iter().find(|(k, _)| *k == kind).unwrap().1, 1,
                "the complete model must stay on the current frame");
            let card = mini_monitor_native::dashboard_layout::layout(orientation, true)
                .cards.into_iter().find(|c| c.kind == kind).unwrap().rect;
            let has_detail_ink = |top, bottom| (top..bottom).any(|y| {
                (card.x + 9..card.x + card.width - 18).any(|x| {
                    let i = ((y * first.width + x) * 4) as usize;
                    let p = &first.pixels[i..i+3];
                    // Detail ink is distinctly blue-grey; antialiased near-white
                    // percentage edges also carry a slight blue tint.
                    p[0] > 90 && p[0] >= p[1].saturating_add(10) && p[1] >= p[2].saturating_add(8)
                })
            });
            assert!(has_detail_ink(card.y + 27, card.y + 41), "model line must be visible");
            assert!(!has_detail_ink(card.y + 41, card.y + 65), "model suffix must never wrap below its row: {model}");
            renderer.set_detail_page(1);
            let second = renderer.render(&input, orientation, true, None).unwrap();
            assert_eq!(first.pixels, second.pixels, "model must never be paged");
        }
    }
}

#[cfg(windows)]
#[test]
fn complete_gpu_name_including_ti_matches_the_full_unabridged_glyph() {
    use mini_monitor_native::{
        dashboard_layout::{layout, CardKind, Orientation},
        dashboard_render::*,
    };
    let model = "NVIDIA GeForce RTX 5070 Ti";
    let glyph = mini_monitor_native::native_text::raster_text(model, 10, [166, 178, 192]).unwrap();
    for orientation in [Orientation::Landscape, Orientation::Portrait] {
        let input = DashboardInput {
            port: String::new(),
            entries: vec![DashboardEntry {
                kind: CardKind::Gpu,
                value: "99%".into(),
                details: vec![model.into()],
                gauge: None,
            }],
        };
        let frame = DashboardRenderer::new()
            .render(&input, orientation, true, None)
            .unwrap();
        let card = layout(orientation, true)
            .cards
            .into_iter()
            .find(|c| c.kind == CardKind::Gpu)
            .unwrap()
            .rect;
        assert!(glyph.width < card.width - 27 && glyph.height <= 14);
        for y in 0..glyph.height {
            for x in 0..glyph.width {
                let src = ((y * glyph.width + x) * 4) as usize;
                let dst = (((card.y + 27 + y) * frame.width + card.x + 9 + x) * 4) as usize;
                let alpha = u16::from(glyph.pixels[src + 3]);
                for (channel, background) in [32u16, 27, 24].into_iter().enumerate() {
                    let expected = (u16::from(glyph.pixels[src + channel])
                        + (background * (255 - alpha) + 127) / 255)
                        .min(255) as u8;
                    assert_eq!(frame.pixels[dst+channel],expected,"complete model glyph, including suffix Ti, must be drawn at one y: {x},{y}");
                }
            }
        }
    }
}

#[cfg(windows)]
#[test]
fn gpu_model_lines_do_not_move_when_usage_changes_between_one_and_two_digits() {
    use mini_monitor_native::{
        dashboard_layout::{layout, CardKind, Orientation},
        dashboard_render::{DashboardEntry, DashboardInput, DashboardRenderer},
    };
    for orientation in [Orientation::Landscape, Orientation::Portrait] {
        for with_api in [false, true] {
            let card = layout(orientation, with_api)
                .cards
                .into_iter()
                .find(|c| c.kind == CardKind::Gpu)
                .unwrap()
                .rect;
            for model in [
                "NVIDIA GeForce RTX 5070 Ti",
                "NVIDIA GeForce RTX 4090 Laptop GPU",
            ] {
                let mut input = DashboardInput {
                    port: String::new(),
                    entries: vec![DashboardEntry {
                        kind: CardKind::Gpu,
                        value: "9%".into(),
                        details: vec![model.into(), "42 C".into(), "44 W".into()],
                        gauge: Some(9.0),
                    }],
                };
                let mut renderer = DashboardRenderer::new();
                // Only model/detail pixels: exclude the numeric and gauge area.
                let details = |frame: &mini_monitor_native::dashboard_render::DashboardFrame| {
                    let mut pixels = Vec::new();
                    for y in card.y + 27..card.y + 63 {
                        for x in card.x + 9..card.x + 100 {
                            let i = ((y * frame.width + x) * 4) as usize;
                            pixels.extend_from_slice(&frame.pixels[i..i + 4]);
                        }
                    }
                    pixels
                };
                for page in [0, 1] {
                    renderer.set_detail_page(page);
                    input.entries[0].value = "9%".into();
                    let reference = renderer
                        .render(&input, orientation, with_api, None)
                        .unwrap();
                    let reference_pixels = details(&reference);
                    for value in ["0%", "1%", "8%", "10%", "11%", "88%", "99%"] {
                        input.entries[0].value = value.into();
                        let frame = renderer
                            .render(&input, orientation, with_api, None)
                            .unwrap();
                        assert!(
                            details(&frame) == reference_pixels,
                            "GPU detail lines moved for {model}, {value}, page {page}, portrait={}",
                            orientation == Orientation::Portrait
                        );
                        assert_eq!(
                            frame.detail_pages, reference.detail_pages,
                            "percentage width must not change model pagination"
                        );
                    }
                }
            }
        }
    }
}
