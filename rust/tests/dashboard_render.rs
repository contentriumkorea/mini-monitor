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
fn long_hardware_names_can_be_paged_instead_of_disappearing() {
    use mini_monitor_native::dashboard_layout::{CardKind, Orientation};
    use mini_monitor_native::dashboard_render::{
        DashboardEntry, DashboardInput, DashboardRenderer,
    };
    let input=DashboardInput {port:"".into(),entries:vec![DashboardEntry {kind:CardKind::Gpu,value:"100%".into(),
        details:vec!["Example NVIDIA GeForce RTX Ultra Long Laptop Workstation Graphics Adapter Name 12345".into()],gauge:Some(100.0)}]};
    let mut renderer = DashboardRenderer::new();
    let first = renderer
        .render(&input, Orientation::Landscape, false, None)
        .unwrap();
    assert!(first
        .detail_pages
        .iter()
        .any(|(kind, pages)| *kind == CardKind::Gpu && *pages > 1));
    renderer.set_detail_page(1);
    let second = renderer
        .render(&input, Orientation::Landscape, false, None)
        .unwrap();
    assert_ne!(first.pixels, second.pixels);
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
