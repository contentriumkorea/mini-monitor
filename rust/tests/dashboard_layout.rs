use mini_monitor_native::{
    dashboard_layout::{layout, vertical_gauge, Orientation},
    dirty::Rect,
};

#[test]
fn landscape_and_portrait_cards_stay_in_bounds_without_overlap() {
    for orientation in [Orientation::Landscape, Orientation::Portrait] {
        for with_api in [false, true] {
            let layout = layout(orientation, with_api);
            assert_eq!(layout.cards.len(), if with_api { 7 } else { 6 });
            for (index, card) in layout.cards.iter().enumerate() {
                assert!(
                    card.rect.x + card.rect.width <= layout.width
                        && card.rect.y + card.rect.height <= layout.height
                );
                for other in &layout.cards[index + 1..] {
                    let a = card.rect;
                    let b = other.rect;
                    assert!(
                        a.x + a.width <= b.x
                            || b.x + b.width <= a.x
                            || a.y + a.height <= b.y
                            || b.y + b.height <= a.y
                    );
                }
            }
        }
    }
}

#[test]
fn vertical_gauge_fills_from_bottom_and_unknown_is_not_zero() {
    let track = Rect {
        x: 5,
        y: 10,
        width: 8,
        height: 60,
    };
    assert_eq!(vertical_gauge(track, None), None);
    assert_eq!(vertical_gauge(track, Some(0.0)), None);
    assert_eq!(
        vertical_gauge(track, Some(50.0)),
        Some(Rect {
            x: 5,
            y: 40,
            width: 8,
            height: 30
        })
    );
    assert_eq!(vertical_gauge(track, Some(100.0)), Some(track));
    assert_eq!(vertical_gauge(track, Some(f64::NAN)), None);
}
