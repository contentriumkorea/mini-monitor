//! Isolated app-server process transport.
use crate::{
    codex_rpc::{encode_request, parse_message, read_frame, RpcMessage},
    codex_runtime::{is_sensitive_environment_key, verify_runtime, BUNDLED_SHA256},
};
use serde_json::Value;
use std::{
    io::{BufReader, Write},
    path::Path,
    process::{Child, Command, Stdio},
    sync::mpsc::{self, Receiver, SyncSender},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type WriteRequest = (Vec<u8>, mpsc::Sender<Result<(), &'static str>>);

/// Owns one subprocess. Neither server responses nor paths are used as error messages.
pub struct RpcProcess {
    child: Child,
    writer: Option<SyncSender<WriteRequest>>,
    incoming: Option<Receiver<Result<RpcMessage, &'static str>>>,
    threads: Vec<JoinHandle<()>>,
}

impl RpcProcess {
    /// `home` must already exist and be the application-owned account directory.
    pub fn spawn_verified(runtime: &Path, home: &Path) -> Result<Self, &'static str> {
        verify_runtime(runtime, BUNDLED_SHA256)?;
        if !runtime.is_absolute() || !home.is_absolute() {
            return Err("invalid runtime path");
        }
        let metadata = std::fs::symlink_metadata(home).map_err(|_| "account home unavailable")?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err("unsafe account home");
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err("unsafe account home");
            }
        }
        let mut command = Command::new(runtime);
        command
            .args([
                "--listen",
                "stdio://",
                "-c",
                "cli_auth_credentials_store=\"file\"",
            ])
            .current_dir(home)
            .env("CODEX_HOME", home);
        for (key, _) in std::env::vars_os() {
            if is_sensitive_environment_key(&key.to_string_lossy()) {
                command.env_remove(key);
            }
        }
        Self::spawn_command(command)
    }

    fn spawn_command(mut command: Command) -> Result<Self, &'static str> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.spawn().map_err(|_| "runtime launch failed")?;
        let Some(mut stdin) = child.stdin.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err("runtime pipe failed");
        };
        let Some(stdout) = child.stdout.take() else {
            let _ = child.kill();
            let _ = child.wait();
            return Err("runtime pipe failed");
        };
        let (write_tx, write_rx) = mpsc::sync_channel::<WriteRequest>(1);
        let writer = thread::spawn(move || {
            while let Ok((frame, ack)) = write_rx.recv() {
                let result = stdin
                    .write_all(&frame)
                    .and_then(|_| stdin.flush())
                    .map_err(|_| "write failed");
                let failed = result.is_err();
                let _ = ack.send(result);
                if failed {
                    break;
                }
            }
        });
        let (read_tx, read_rx) = mpsc::sync_channel(32);
        let reader = thread::spawn(move || {
            let mut stdout = BufReader::new(stdout);
            loop {
                let message = match read_frame(&mut stdout) {
                    Ok(Some(frame)) => parse_message(&frame),
                    Ok(None) => Err("runtime exited"),
                    Err(_) => Err("invalid runtime response"),
                };
                let failed = message.is_err();
                if read_tx.send(message).is_err() || failed {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            writer: Some(write_tx),
            incoming: Some(read_rx),
            threads: vec![writer, reader],
        })
    }

    pub fn request<F>(
        &mut self,
        id: u64,
        method: &str,
        params: Value,
        timeout: Duration,
        mut notify: F,
    ) -> Result<Value, &'static str>
    where
        F: FnMut(&str, &Value),
    {
        if timeout.is_zero() || timeout > Duration::from_secs(30) {
            return Err("invalid timeout");
        }
        let deadline = Instant::now() + timeout;
        let frame = encode_request(id, method, params)?;
        self.write_frame(frame, deadline)?;
        loop {
            let message = self
                .incoming
                .as_ref()
                .ok_or("runtime closed")?
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .map_err(|_| "runtime timeout")??;
            match message {
                RpcMessage::Response {
                    id: received,
                    result,
                    error,
                } if received == id => {
                    return if error.is_some() {
                        Err("runtime request failed")
                    } else {
                        result.ok_or("invalid response")
                    };
                }
                RpcMessage::Notification { method, params } => notify(&method, &params),
                _ => (),
            }
        }
    }

    fn write_frame(&self, frame: Vec<u8>, deadline: Instant) -> Result<(), &'static str> {
        let (ack_tx, ack_rx) = mpsc::channel();
        self.writer
            .as_ref()
            .ok_or("runtime closed")?
            .try_send((frame, ack_tx))
            .map_err(|_| "runtime busy")?;
        ack_rx
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .map_err(|_| "runtime timeout")??;
        Ok(())
    }

    /// Drain notifications while idle; stale responses cannot be mistaken for events.
    pub fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        let incoming = self.incoming.as_ref().ok_or("runtime closed")?;
        for _ in 0..32 {
            match incoming.try_recv() {
                Ok(Ok(RpcMessage::Notification { method, params })) => {
                    return Ok(Some((method, params)))
                }
                Ok(Ok(RpcMessage::Response { .. })) => (),
                Ok(Err(error)) => return Err(error),
                Err(mpsc::TryRecvError::Empty) => return Ok(None),
                Err(mpsc::TryRecvError::Disconnected) => return Err("runtime closed"),
            }
        }
        Ok(None)
    }

    /// Perform the required app-server handshake. Request ID 1 is reserved here.
    pub fn initialize(&mut self, timeout: Duration) -> Result<(), &'static str> {
        if timeout.is_zero() || timeout > Duration::from_secs(30) {
            return Err("invalid timeout");
        }
        let deadline = Instant::now() + timeout;
        self.request(
            1,
            "initialize",
            serde_json::json!({"clientInfo": {
                "name": "mini_monitor", "title": "Mini Monitor", "version": "1.0.0"
            }}),
            timeout,
            |_, _| {},
        )?;
        self.write_frame(
            b"{\"method\":\"initialized\",\"params\":{}}\n".to_vec(),
            deadline,
        )
    }
}

impl Drop for RpcProcess {
    fn drop(&mut self) {
        self.writer.take();
        self.incoming.take();
        let _ = self.child.kill();
        let _ = self.child.wait();
        for thread in self.threads.drain(..) {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{process::Command, time::Duration};

    #[test]
    fn mock_process_round_trips_and_handles_notification_before_response() {
        let mut command = Command::new("python");
        command.args([
            "-u",
            "-c",
            r#"import sys,json
for line in sys.stdin:
    msg=json.loads(line)
    print(json.dumps({'method':'account/updated','params':{'authMode':'chatgpt'}}),flush=True)
    print(json.dumps({'id':msg['id'],'result':{'ok':True}}),flush=True)
"#,
        ]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        let mut notifications = 0;
        let result = process
            .request(
                3,
                "account/read",
                json!({}),
                Duration::from_secs(3),
                |_, _| {
                    notifications += 1;
                },
            )
            .unwrap();
        assert_eq!(result, json!({"ok":true}));
        assert_eq!(notifications, 1);
    }

    #[test]
    fn silent_mock_process_times_out_without_hanging_shutdown() {
        let mut command = Command::new("python");
        command.args(["-u", "-c", "import time; time.sleep(20)"]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        let started = std::time::Instant::now();
        assert!(process
            .request(
                1,
                "account/read",
                json!({}),
                Duration::from_millis(150),
                |_, _| {}
            )
            .is_err());
        drop(process);
        assert!(started.elapsed() < Duration::from_secs(4));
    }

    #[test]
    fn oversized_runtime_response_is_rejected_without_disclosing_contents() {
        let mut command = Command::new("python");
        command.args([
            "-u",
            "-c",
            "import sys; sys.stdin.readline(); print('x' * (1024 * 1024 + 10),flush=True)",
        ]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        let error = process
            .request(
                1,
                "account/read",
                json!({}),
                Duration::from_secs(3),
                |_, _| {},
            )
            .unwrap_err();
        assert_eq!(error, "invalid runtime response");
    }

    #[test]
    fn flooding_notifications_cannot_block_drop() {
        let mut command = Command::new("python");
        command.args([
            "-u",
            "-c",
            r#"import sys,json,time
msg=json.loads(sys.stdin.readline())
print(json.dumps({'id':msg['id'],'result':{}}),flush=True)
for i in range(1000): print(json.dumps({'method':'updated','params':{}}),flush=True)
time.sleep(20)
"#,
        ]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        process
            .request(
                1,
                "account/read",
                json!({}),
                Duration::from_secs(3),
                |_, _| {},
            )
            .unwrap();
        std::thread::sleep(Duration::from_millis(150));
        let started = std::time::Instant::now();
        drop(process);
        assert!(started.elapsed() < Duration::from_secs(4));
    }

    #[test]
    fn initialize_sends_required_initialized_notification() {
        let mut command = Command::new("python");
        command.args(["-u", "-c", r#"import sys,json
first=json.loads(sys.stdin.readline())
print(json.dumps({'id':first['id'],'result':{}}),flush=True)
notification=json.loads(sys.stdin.readline())
second=json.loads(sys.stdin.readline())
print(json.dumps({'id':second['id'],'result':{'initialized':notification.get('method')=='initialized' and 'id' not in notification}}),flush=True)
"#]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        process.initialize(Duration::from_secs(3)).unwrap();
        let result = process
            .request(
                2,
                "account/read",
                json!({}),
                Duration::from_secs(3),
                |_, _| {},
            )
            .unwrap();
        assert_eq!(result, json!({"initialized":true}));
    }

    #[test]
    fn notifications_after_response_are_available_without_another_request() {
        let mut command = Command::new("python");
        command.args(["-u", "-c", r#"import sys,json,time
msg=json.loads(sys.stdin.readline())
print(json.dumps({'id':msg['id'],'result':{}}),flush=True)
time.sleep(0.05)
print(json.dumps({'method':'account/login/completed','params':{'loginId':'fake','success':True}}),flush=True)
time.sleep(1)
"#]);
        let mut process = RpcProcess::spawn_command(command).unwrap();
        process
            .request(
                1,
                "account/login/start",
                json!({}),
                Duration::from_secs(3),
                |_, _| {},
            )
            .unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        let mut seen = false;
        while std::time::Instant::now() < deadline {
            if let Some((method, _)) = process.poll_notification().unwrap() {
                seen = method == "account/login/completed";
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(seen);
    }
}
