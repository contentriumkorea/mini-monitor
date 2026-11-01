//! Explicit account refresh. Account data and transport responses are never logged.
use crate::{
    codex::{parse_limits, Limits},
    codex_login::{LoginAction, LoginFlow},
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
    fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        Ok(None)
    }
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
    fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        RpcProcess::poll_notification(self)
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
    pending_cancel: Option<String>,
}

impl<T: RpcTransport> AccountClient<T> {
    /// Runtime initialize uses ID 1; all account calls begin at ID 2.
    pub fn new(transport: T) -> Self {
        Self {
            transport,
            next_id: 2,
            pending_cancel: None,
        }
    }
    pub fn into_transport(self) -> T {
        self.transport
    }

    pub fn cancel_server_login<F>(
        &mut self,
        login_id: &str,
        mut notify: F,
    ) -> Result<(), &'static str>
    where
        F: FnMut(&str, &Value),
    {
        if login_id.is_empty() || login_id.len() > 512 || login_id.chars().any(char::is_control) {
            return Err("invalid login ID");
        }
        if self
            .pending_cancel
            .as_deref()
            .is_some_and(|pending| pending != login_id)
        {
            return Err("another cancellation pending");
        }
        self.pending_cancel = Some(login_id.to_owned());
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).ok_or("request ID exhausted")?;
        self.transport.request(
            id,
            "account/login/cancel",
            json!({"loginId":login_id}),
            &mut notify,
        )?;
        self.pending_cancel = None;
        Ok(())
    }

    pub fn cancel_login<F>(&mut self, flow: &mut LoginFlow, notify: F) -> Result<(), &'static str>
    where
        F: FnMut(&str, &Value),
    {
        let id = match flow.cancel() {
            LoginAction::CancelServer(id) => Some(id),
            _ => self.pending_cancel.clone(),
        };
        match id {
            Some(id) => self.cancel_server_login(&id, notify),
            None => Ok(()),
        }
    }

    /// Consume a bounded number of queued events without issuing an extra RPC.
    pub fn poll_login<F>(
        &mut self,
        flow: &mut LoginFlow,
        mut notify: F,
    ) -> Result<LoginAction, &'static str>
    where
        F: FnMut(&str, &Value),
    {
        for _ in 0..32 {
            let Some((method, params)) = self.transport.poll_notification()? else {
                return Ok(LoginAction::None);
            };
            let action = if method == "account/login/completed" {
                params
                    .get("loginId")
                    .and_then(Value::as_str)
                    .map(|id| {
                        flow.completed(
                            id,
                            params.get("success").and_then(Value::as_bool) == Some(true),
                        )
                    })
                    .unwrap_or(LoginAction::None)
            } else {
                LoginAction::None
            };
            notify(&method, &params);
            if !matches!(action, LoginAction::None) {
                return Ok(action);
            }
        }
        Ok(LoginAction::None)
    }

    /// Returns an action for the UI/worker to execute; does not open a browser itself.
    pub fn begin_login<F>(
        &mut self,
        flow: &mut LoginFlow,
        mut notify: F,
    ) -> Result<LoginAction, &'static str>
    where
        F: FnMut(&str, &Value),
    {
        if self.pending_cancel.is_some() {
            return Err("cancellation pending");
        }
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).ok_or("request ID exhausted")?;
        let generation = flow.begin().ok_or("login already pending")?;
        let result = self.transport.request(
            id,
            "account/login/start",
            json!({"type":"chatgpt"}),
            &mut |method, params| {
                if method == "account/login/completed" {
                    if let Some(login_id) = params.get("loginId").and_then(Value::as_str) {
                        let _ = flow.completed(
                            login_id,
                            params.get("success").and_then(Value::as_bool) == Some(true),
                        );
                    }
                }
                notify(method, params);
            },
        );
        let result = match result {
            Ok(value) => value,
            Err(error) => {
                flow.cancel();
                return Err(error);
            }
        };
        let Some(login_id) = result.get("loginId").and_then(Value::as_str) else {
            flow.cancel();
            return Err("invalid login response");
        };
        let url = result.get("authUrl").and_then(Value::as_str).unwrap_or("");
        let action = flow.started(generation, login_id, url);
        if let LoginAction::CancelServer(id) = &action {
            self.pending_cancel = Some(id.clone());
        }
        Ok(action)
    }

    pub fn refresh<F>(&mut self, mut notify: F) -> Result<AccountState, &'static str>
    where
        F: FnMut(&str, &Value),
    {
        let account_changed = std::cell::Cell::new(false);
        let mut on_event = |method: &str, params: &Value| {
            if method == "account/updated" {
                account_changed.set(true);
            }
            notify(method, params);
        };
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).ok_or("request ID exhausted")?;
        let response = self.transport.request(
            id,
            "account/read",
            json!({"refreshToken":false}),
            &mut on_event,
        )?;
        if account_changed.get() {
            return Err("account changed during refresh");
        }
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
                .request(id, "account/rateLimits/read", json!({}), &mut on_event)?;
        if account_changed.get() {
            return Err("account changed during refresh");
        }
        let encoded = serde_json::to_string(&limits).map_err(|_| "invalid limits response")?;
        Ok(AccountState::Ready {
            email,
            plan,
            limits: parse_limits(&encoded)?,
        })
    }
}
