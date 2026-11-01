#[cfg(windows)]
fn main() {
    match mini_monitor_native::cli::run(std::env::args().skip(1).collect()) {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!(
                "Mini Monitor command failed: {}",
                if error.kind() == std::io::ErrorKind::InvalidInput {
                    "invalid options or settings"
                } else {
                    "operation unavailable"
                }
            );
            std::process::exit(if error.kind() == std::io::ErrorKind::InvalidInput {
                2
            } else {
                1
            });
        }
    }
}
#[cfg(not(windows))]
fn main() {
    println!("Mini Monitor {}", env!("CARGO_PKG_VERSION"));
}
