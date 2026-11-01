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
