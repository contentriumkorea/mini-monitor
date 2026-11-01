//! Native migration components. Not yet a complete application.
pub mod account_worker;
pub mod budget;
pub mod budget_cycle;
pub mod codex;
pub mod codex_client;
pub mod codex_login;
pub mod codex_process;
pub mod codex_rpc;
pub mod codex_runtime;
pub mod config;
pub mod costs_http;
pub mod dashboard_layout;
#[cfg(windows)]
pub mod dashboard_render;
pub mod device;
pub mod dirty;
pub mod gpu;
pub mod latest_queue;
pub mod layered_window;
#[cfg(windows)]
pub mod native_icon;
#[cfg(windows)]
pub mod native_text;
pub mod readouts;
#[cfg(windows)]
pub mod secrets;
#[cfg(windows)]
pub mod sensor_worker;
pub mod sensors;
pub mod taskbar_geometry;
#[cfg(windows)]
pub mod taskbar_render;
#[cfg(windows)]
pub mod taskbar_shell;
#[cfg(windows)]
pub mod taskbar_window;
pub mod telemetry;
