use mini_monitor_native::latest_queue::LatestQueue;
use std::time::Duration;

#[test]
fn slow_consumer_receives_only_most_recent_update() {
    let queue = LatestQueue::new();
    for n in 0..100_000 {
        queue.submit(n).unwrap();
    }
    assert_eq!(queue.take_timeout(Duration::ZERO), Some(99_999));
    assert_eq!(queue.take_timeout(Duration::ZERO), None);
}

#[test]
fn closing_queue_discards_pending_and_refuses_new_work() {
    let queue = LatestQueue::new();
    queue.submit(1).unwrap();
    queue.close();
    assert_eq!(queue.take_timeout(Duration::ZERO), None);
    assert!(queue.submit(2).is_err());
}

#[test]
fn closing_a_waiting_queue_wakes_the_consumer_promptly() {
    use std::{
        sync::{mpsc, Arc},
        thread,
        time::Instant,
    };
    let queue = Arc::new(LatestQueue::<u8>::new());
    let consumer = Arc::clone(&queue);
    let (ready_tx, ready_rx) = mpsc::channel();
    let thread = thread::spawn(move || {
        ready_tx.send(()).unwrap();
        consumer.take_timeout(Duration::from_secs(10))
    });
    ready_rx.recv().unwrap();
    let started = Instant::now();
    queue.close();
    assert_eq!(thread.join().unwrap(), None);
    assert!(started.elapsed() < Duration::from_secs(2));
}
