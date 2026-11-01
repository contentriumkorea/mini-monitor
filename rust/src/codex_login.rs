//! Login event ordering, without logging URLs or login identifiers.
use crate::codex::trusted_login_url;

// Intentionally no Debug: both URL and login ID can be sensitive.
pub enum LoginAction {
    None,
    OpenBrowser(String),
    CancelServer(String),
    Refresh,
    Failed,
}

enum State {
    Idle,
    Starting { early: Vec<(String, bool)> },
    Pending { id: String },
}

pub struct LoginFlow {
    generation: u64,
    state: State,
}

impl Default for LoginFlow {
    fn default() -> Self {
        Self {
            generation: 0,
            state: State::Idle,
        }
    }
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 512 && !id.chars().any(char::is_control)
}

impl LoginFlow {
    pub fn is_pending(&self) -> bool {
        !matches!(self.state, State::Idle)
    }
    pub fn begin(&mut self) -> Option<u64> {
        if !matches!(self.state, State::Idle) {
            return None;
        }
        self.generation = self.generation.checked_add(1)?;
        self.state = State::Starting { early: Vec::new() };
        Some(self.generation)
    }

    pub fn cancel(&mut self) -> LoginAction {
        let old = std::mem::replace(&mut self.state, State::Idle);
        match old {
            State::Pending { id } => LoginAction::CancelServer(id),
            _ => LoginAction::None,
        }
    }

    pub fn started(&mut self, generation: u64, id: &str, url: &str) -> LoginAction {
        if !valid_id(id) {
            if generation == self.generation {
                self.state = State::Idle;
            }
            return LoginAction::Failed;
        }
        if generation != self.generation || !matches!(self.state, State::Starting { .. }) {
            return LoginAction::CancelServer(id.to_owned());
        }
        if !trusted_login_url(url) {
            self.state = State::Idle;
            return LoginAction::CancelServer(id.to_owned());
        }
        let State::Starting { early } = std::mem::replace(&mut self.state, State::Idle) else {
            unreachable!();
        };
        if let Some((_, success)) = early.into_iter().find(|(early_id, _)| early_id == id) {
            return if success {
                LoginAction::Refresh
            } else {
                LoginAction::Failed
            };
        }
        self.state = State::Pending { id: id.to_owned() };
        LoginAction::OpenBrowser(url.to_owned())
    }

    pub fn completed(&mut self, id: &str, success: bool) -> LoginAction {
        if !valid_id(id) {
            return LoginAction::None;
        }
        match &mut self.state {
            State::Starting { early } => {
                if early.len() >= 8 {
                    self.state = State::Idle;
                    return LoginAction::Failed;
                }
                early.push((id.to_owned(), success));
                LoginAction::None
            }
            State::Pending { id: expected } if expected == id => {
                self.state = State::Idle;
                if success {
                    LoginAction::Refresh
                } else {
                    LoginAction::Failed
                }
            }
            _ => LoginAction::None,
        }
    }
}
