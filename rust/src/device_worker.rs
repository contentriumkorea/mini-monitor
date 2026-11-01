//! One owner for USB writes. Does not open a device unless explicitly started.
use crate::{
    device::{BitmapTransfer, DeviceInfo, FrameDelta, Orientation},
    device_connect::ConnectGate,
    device_session::DeviceSession,
    latest_queue::LatestQueue,
};
use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WorkerStatus {
    Starting,
    Ready,
    Stopped,
    Error,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WorkerNotReady;

pub struct DeviceWorker {
    gate: Arc<ConnectGate>,
    frames: Arc<LatestQueue<BitmapTransfer>>,
    status: Arc<Mutex<WorkerStatus>>,
    settings: Arc<Mutex<Option<(u8, Orientation)>>>,
    thread: Option<JoinHandle<()>>,
}
impl DeviceWorker {
    /// Starts the Windows transport only after the worker has been explicitly requested.
    /// `manual_port` never bypasses USB identity checks.
    #[cfg(windows)]
    pub fn start_physical(
        brightness: u8,
        orientation: Orientation,
        manual_port: Option<String>,
    ) -> io::Result<Self> {
        Self::start(
            brightness,
            orientation,
            move |gate| crate::windows_serial::connect(manual_port.as_deref(), gate),
            crate::device_discovery::discover,
        )
    }
    /// The supplied connection function must validate identity before and after opening.
    /// `join` may wait for OS I/O and must not be called on the UI thread.
    pub fn start<W, F, D>(
        brightness: u8,
        orientation: Orientation,
        connect: F,
        mut discover: D,
    ) -> io::Result<Self>
    where
        W: Write + Send + 'static,
        F: FnOnce(&ConnectGate) -> io::Result<DeviceSession<W>> + Send + 'static,
        D: FnMut() -> io::Result<Vec<DeviceInfo>> + Send + 'static,
    {
        if !(1..=50).contains(&brightness) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid brightness",
            ));
        }
        let gate = Arc::new(ConnectGate::default());
        let frames = Arc::new(LatestQueue::<BitmapTransfer>::new());
        let status = Arc::new(Mutex::new(WorkerStatus::Starting));
        let settings = Arc::new(Mutex::new(None));
        let pending_settings = settings.clone();
        let (g, q, s) = (gate.clone(), frames.clone(), status.clone());
        let thread = thread::Builder::new()
            .name("mini-monitor-usb".into())
            .spawn(move || {
                let result = (|| -> io::Result<()> {
                    let mut session = connect(&g)?;
                    session.revalidate(&discover()?)?;
                    g.perform_if_active(|| {
                        session.start_cancellable(
                            brightness,
                            orientation,
                            |duration| {
                                let deadline = Instant::now() + duration;
                                while Instant::now() < deadline {
                                    if g.is_cancelled() {
                                        return false;
                                    }
                                    thread::sleep(
                                        deadline
                                            .saturating_duration_since(Instant::now())
                                            .min(Duration::from_millis(20)),
                                    );
                                }
                                !g.is_cancelled()
                            },
                            || g.is_cancelled(),
                        )
                    })?;
                    *s.lock().unwrap_or_else(|e| e.into_inner()) = WorkerStatus::Ready;
                    let (mut brightness, mut orientation) = (brightness, orientation);
                    let mut last_sent: Option<BitmapTransfer> = None;
                    while !g.is_cancelled() {
                        let frame = q.take_timeout(Duration::from_millis(500));
                        if g.is_cancelled() {
                            break;
                        }
                        session.revalidate(&discover()?)?;
                        let update = pending_settings
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .take();
                        if let Some((next_brightness, next_orientation)) = update {
                            if next_orientation != orientation {
                                g.perform_if_active(|| {
                                    session.set_orientation(
                                        next_orientation,
                                        next_brightness,
                                        |duration| {
                                            let deadline = Instant::now() + duration;
                                            while Instant::now() < deadline {
                                                if g.is_cancelled() {
                                                    return false;
                                                }
                                                thread::sleep(
                                                    deadline
                                                        .saturating_duration_since(Instant::now())
                                                        .min(Duration::from_millis(20)),
                                                );
                                            }
                                            !g.is_cancelled()
                                        },
                                    )
                                })?;
                                last_sent = None;
                            } else if next_brightness != brightness {
                                g.perform_if_active(|| session.set_brightness(next_brightness))?;
                            }
                            brightness = next_brightness;
                            orientation = next_orientation;
                        }
                        if let Some(frame) = frame {
                            let (width, height) = match orientation {
                                Orientation::Portrait | Orientation::ReversePortrait => (320, 480),
                                _ => (480, 320),
                            };
                            if !frame.is_full_for(width, height) {
                                continue;
                            }
                            let delta = match last_sent.as_ref() {
                                Some(previous) => {
                                    frame.delta_from(previous).map_err(io::Error::other)?
                                }
                                None => FrameDelta::Full,
                            };
                            match delta {
                                FrameDelta::Unchanged => continue,
                                FrameDelta::Full => g.perform_if_active(|| {
                                    session.send_full_cancellable(&frame, || g.is_cancelled())
                                })?,
                                FrameDelta::Region(region) => g.perform_if_active(|| {
                                    session.send_partial_cancellable(&region, || g.is_cancelled())
                                })?,
                            }
                            last_sent = Some(frame);
                        }
                    }
                    Ok(())
                })();
                q.close();
                *s.lock().unwrap_or_else(|e| e.into_inner()) = if g.is_cancelled() {
                    WorkerStatus::Stopped
                } else if result.is_err() {
                    WorkerStatus::Error
                } else {
                    WorkerStatus::Stopped
                };
            })?;
        Ok(Self {
            gate,
            frames,
            status,
            settings,
            thread: Some(thread),
        })
    }
    pub fn status(&self) -> WorkerStatus {
        *self.status.lock().unwrap_or_else(|e| e.into_inner())
    }
    /// New frames replace stale unsent frames; no unbounded queue can accumulate.
    pub fn submit_full(&self, frame: BitmapTransfer) -> Result<(), WorkerNotReady> {
        if self.status() != WorkerStatus::Ready {
            return Err(WorkerNotReady);
        }
        self.frames.submit(frame).map_err(|_| WorkerNotReady)
    }
    /// Coalesce changes; they are applied by the same thread that owns the display.
    pub fn set_display(&self, brightness: u8, orientation: Orientation) -> io::Result<()> {
        if !(1..=50).contains(&brightness) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid brightness",
            ));
        }
        if self.status() != WorkerStatus::Ready || self.gate.is_cancelled() {
            return Err(io::Error::other("display not ready"));
        }
        *self.settings.lock().unwrap_or_else(|e| e.into_inner()) = Some((brightness, orientation));
        Ok(())
    }
    pub fn request_stop(&self) {
        self.gate.request_cancel();
        self.frames.close();
    }
    pub fn join(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
impl Drop for DeviceWorker {
    fn drop(&mut self) {
        self.request_stop();
    }
}
