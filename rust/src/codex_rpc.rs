//! Bounded JSON-RPC wire format; deliberately no Debug impl for sensitive payloads.
use serde_json::{json, Value};
use std::io::{BufRead, Read};
const MAX_LINE: usize = 1024 * 1024;

/// Read a single complete line without allowing an untrusted server to grow memory without bound.
pub fn read_frame<R: BufRead>(reader: &mut R) -> Result<Option<Vec<u8>>, &'static str> {
    let mut frame = Vec::new();
    let count = reader
        .take((MAX_LINE + 1) as u64)
        .read_until(b'\n', &mut frame)
        .map_err(|_| "read failed")?;
    if count == 0 {
        return Ok(None);
    }
    if count > MAX_LINE || frame.last() != Some(&b'\n') {
        return Err("invalid frame");
    }
    Ok(Some(frame))
}

pub enum RpcMessage {
    Response {
        id: u64,
        result: Option<Value>,
        error: Option<Value>,
    },
    Notification {
        method: String,
        params: Value,
    },
}

pub fn encode_request(id: u64, method: &str, params: Value) -> Result<Vec<u8>, &'static str> {
    if method.is_empty()
        || method.len() > 128
        || !method
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'/')
    {
        return Err("invalid method");
    }
    let mut wire = serde_json::to_vec(&json!({"id": id, "method": method, "params": params}))
        .map_err(|_| "encode error")?;
    if wire.len() > MAX_LINE {
        return Err("request too large");
    }
    wire.push(b'\n');
    Ok(wire)
}

pub fn parse_message(line: &[u8]) -> Result<RpcMessage, &'static str> {
    if line.len() > MAX_LINE {
        return Err("response too large");
    }
    let message: Value = serde_json::from_slice(line).map_err(|_| "invalid response")?;
    let object = message.as_object().ok_or("invalid response")?;
    if let Some(id) = object.get("id") {
        let id = id.as_u64().ok_or("invalid id")?;
        let result = object.get("result").cloned();
        let error = object.get("error").cloned();
        if result.is_some() == error.is_some() {
            return Err("invalid response shape");
        }
        Ok(RpcMessage::Response { id, result, error })
    } else {
        let method = object
            .get("method")
            .and_then(Value::as_str)
            .filter(|m| !m.is_empty() && m.len() <= 128)
            .ok_or("invalid notification")?;
        Ok(RpcMessage::Notification {
            method: method.to_owned(),
            params: object.get("params").cloned().unwrap_or(Value::Null),
        })
    }
}
