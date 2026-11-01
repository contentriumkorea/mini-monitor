//! Recent diagnostic measurements with a strict memory ceiling.
use std::collections::VecDeque;

pub struct LatencyWindow {
    samples: VecDeque<f64>,
    capacity: usize,
}

impl LatencyWindow {
    pub fn new(capacity: usize) -> Result<Self, &'static str> {
        if !(1..=10_000).contains(&capacity) {
            return Err("invalid capacity");
        }
        Ok(Self {
            samples: VecDeque::with_capacity(capacity),
            capacity,
        })
    }

    pub fn record(&mut self, value: f64) {
        if !value.is_finite() || value < 0.0 {
            return;
        }
        if self.samples.len() == self.capacity {
            self.samples.pop_front();
        }
        self.samples.push_back(value);
    }

    pub fn len(&self) -> usize {
        self.samples.len()
    }
    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// Same nearest-rank convention as the previous app, over recent samples only.
    pub fn p95(&self) -> Option<f64> {
        if self.samples.is_empty() {
            return None;
        }
        let mut values: Vec<_> = self.samples.iter().copied().collect();
        let rank = (values.len() * 95).div_ceil(100) - 1;
        values.select_nth_unstable_by(rank, f64::total_cmp);
        Some(values[rank])
    }
}
