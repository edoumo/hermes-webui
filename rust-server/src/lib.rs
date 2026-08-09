//! Hermes WebUI Rust port — library root.
//!
//! Exposes the modules so integration tests (tests/) can exercise the router
//! as an external consumer, matching how the upstream Python server is tested.

pub mod app;
pub mod auth;
pub mod config;
pub mod error;
pub mod hermes;
pub mod routes;
pub mod sessions;
pub mod state;
