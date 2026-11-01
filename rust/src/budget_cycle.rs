//! Reject late API responses after a budget edit or monthly reset.
use crate::budget::{monthly_period, USD_SCALE};
use time::Date;

#[derive(Clone, Copy)]
pub struct PendingBudget {
    id: u64,
    start: Date,
    end: Date,
}

// No Debug: spending data are private.
pub struct BudgetSnapshot {
    pub period_start: Date,
    pub period_end: Date,
    pub total_cents: u64,
    pub spent_units: u128,
    pub used_percent: f64,
}

pub struct BudgetCycle {
    anchor: Date,
    total_cents: u64,
    sequence: u64,
    active: Option<u64>,
}

impl BudgetCycle {
    pub fn new(anchor: Date, total_cents: u64) -> Result<Self, &'static str> {
        if !(1..=99_999_999).contains(&total_cents) {
            return Err("invalid total");
        }
        Ok(Self {
            anchor,
            total_cents,
            sequence: 0,
            active: None,
        })
    }

    pub fn reconfigure(&mut self, anchor: Date, total_cents: u64) -> Result<(), &'static str> {
        if !(1..=99_999_999).contains(&total_cents) {
            return Err("invalid total");
        }
        let sequence = self.sequence.checked_add(1).ok_or("request ID exhausted")?;
        self.anchor = anchor;
        self.total_cents = total_cents;
        self.sequence = sequence;
        self.active = None;
        Ok(())
    }

    pub fn begin(&mut self, today_utc: Date) -> Result<PendingBudget, &'static str> {
        let (start, end) = monthly_period(self.anchor, today_utc)?;
        self.sequence = self.sequence.checked_add(1).ok_or("request ID exhausted")?;
        self.active = Some(self.sequence);
        Ok(PendingBudget {
            id: self.sequence,
            start,
            end,
        })
    }

    pub fn complete(
        &mut self,
        pending: PendingBudget,
        today_utc: Date,
        spent_units: u128,
    ) -> Result<BudgetSnapshot, &'static str> {
        if self.active != Some(pending.id) {
            return Err("stale request");
        }
        self.active = None;
        if monthly_period(self.anchor, today_utc)? != (pending.start, pending.end) {
            return Err("period changed");
        }
        if spent_units > USD_SCALE * 1_000_000_000_000 {
            return Err("invalid spend");
        }
        Ok(BudgetSnapshot {
            period_start: pending.start,
            period_end: pending.end,
            total_cents: self.total_cents,
            spent_units,
            used_percent: (spent_units as f64 / USD_SCALE as f64) * 10_000.0
                / self.total_cents as f64,
        })
    }
}
