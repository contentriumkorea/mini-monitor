//! Explicit account refresh. Account data and transport responses are never logged.
use crate::{
    codex::{parse_limits, Limits},
    codex_process::RpcProcess,
};
use serde_json::{json, Value};
use std::time::Duration;

pub trait RpcTransport {
    fn request(
        &mut self,
        id: u64,
        method: &str,
        params: Value,
        notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str>;
}

impl RpcTransport for RpcProcess {
    fn request(
        &mut self,
        id: u64,
        method: &str,
        params: Value,
        notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        RpcProcess::request(self, id, method, params, Duration::from_secs(8), notify)
    }
}

// No Debug implementation: email and account usage are private.
pub enum AccountState {
    SignedOut,
    Ready {
        email: Option<String>,
        plan: Option<String>,
        limits: Limits,
    },
}

pub struct AccountClient<T> {
    transport: T,
    next_id: u64,
}

impl<T: RpcTransport> AccountClient<T> {
    /// Runtime initialize uses ID 1; all account calls begin at ID 2.
    pub fn new(transport: T) -> Self {
        Self {
            transport,
            next_id: 2,
        }
    }
    pub fn into_transport(self) -> T {
        self.transport
    }

    pub fn refresh<F>(&mut self, mut notify: F) -> Result<AccountState, &'static str>
    where
        F: FnMut(&str, &Value),
    {
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).ok_or("request ID exhausted")?;
        let response = self.transport.request(
            id,
            "account/read",
            json!({"refreshToken":false}),
            &mut notify,
        )?;
        let account = response.get("account").ok_or("invalid account response")?;
        if account.is_null() {
            return Ok(AccountState::SignedOut);
        }
        if account.get("type").and_then(Value::as_str) != Some("chatgpt") {
            return Err("unsupported account");
        }
        let field = |key: &str, max: usize| -> Result<Option<String>, &'static str> {
            match account.get(key) {
                None | Some(Value::Null) => Ok(None),
                Some(Value::String(value))
                    if value.len() <= max && !value.chars().any(char::is_control) =>
                {
                    Ok(Some(value.clone()))
                }
                _ => Err("invalid account response"),
            }
        };
        let email = field("email", 320)?;
        let plan = field("planType", 64)?;
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).ok_or("request ID exhausted")?;
        let limits =
            self.transport
                .request(id, "account/rateLimits/read", json!({}), &mut notify)?;
        let encoded = serde_json::to_string(&limits).map_err(|_| "invalid limits response")?;
        Ok(AccountState::Ready {
            email,
            plan,
            limits: parse_limits(&encoded)?,
        })
    }
}
