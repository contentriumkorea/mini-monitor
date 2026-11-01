//! Official organization cost endpoint. Never expose secrets or response text in errors.
use crate::budget::collect_costs;
use reqwest::{
    blocking::{Client, Request},
    header::{HeaderValue, ACCEPT, AUTHORIZATION},
    redirect::Policy,
};
use std::{io::Read, time::Duration};

const ENDPOINT: &str = "https://api.openai.com/v1/organization/costs";
const MAX_RESPONSE: u64 = 4_000_000;

pub struct CostsHttpClient {
    client: Client,
    authorization: HeaderValue,
}

impl CostsHttpClient {
    pub fn new(key: &str) -> Result<Self, &'static str> {
        if !(16..=1024).contains(&key.len())
            || !key.is_ascii()
            || key
                .bytes()
                .any(|b| b.is_ascii_whitespace() || b.is_ascii_control())
        {
            return Err("invalid key format");
        }
        let mut authorization =
            HeaderValue::from_str(&format!("Bearer {key}")).map_err(|_| "invalid key format")?;
        authorization.set_sensitive(true);
        let client = Client::builder()
            .https_only(true)
            .redirect(Policy::none())
            .connect_timeout(Duration::from_secs(7))
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(|_| "client unavailable")?;
        Ok(Self {
            client,
            authorization,
        })
    }

    pub fn build_request(
        &self,
        start: i64,
        end: i64,
        cursor: Option<&str>,
    ) -> Result<Request, &'static str> {
        if end <= start || cursor.is_some_and(|s| s.is_empty() || s.len() > 2048) {
            return Err("invalid request");
        }
        let mut builder = self
            .client
            .get(ENDPOINT)
            .header(ACCEPT, "application/json")
            .query(&[
                ("start_time", start.to_string()),
                ("end_time", end.to_string()),
                ("bucket_width", "1d".to_owned()),
                ("limit", "180".to_owned()),
            ]);
        if let Some(cursor) = cursor {
            builder = builder.query(&[("page", cursor)]);
        }
        let mut request = builder.build().map_err(|_| "request invalid")?;
        request
            .headers_mut()
            .insert(AUTHORIZATION, self.authorization.clone());
        Ok(request)
    }

    fn fetch_page(
        &self,
        start: i64,
        end: i64,
        cursor: Option<&str>,
    ) -> Result<String, &'static str> {
        let request = self.build_request(start, end, cursor)?;
        let response = self
            .client
            .execute(request)
            .map_err(|_| "network failure")?;
        match response.status().as_u16() {
            200 => (),
            401 => return Err("unauthorized"),
            403 => return Err("forbidden"),
            429 => return Err("rate limited"),
            _ => return Err("unexpected status"),
        }
        if response.content_length().is_some_and(|n| n > MAX_RESPONSE) {
            return Err("response too large");
        }
        let mut data = Vec::new();
        response
            .take(MAX_RESPONSE + 1)
            .read_to_end(&mut data)
            .map_err(|_| "response read failed")?;
        if data.len() as u64 > MAX_RESPONSE {
            return Err("response too large");
        }
        String::from_utf8(data).map_err(|_| "invalid response encoding")
    }

    pub fn fetch_spend(
        &self,
        start: i64,
        end: i64,
        cancelled: impl Fn() -> bool,
    ) -> Result<u128, &'static str> {
        collect_costs(
            start,
            end,
            |cursor| self.fetch_page(start, end, cursor),
            cancelled,
        )
    }
}
