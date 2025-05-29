use std::path::PathBuf;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum AppError {
    #[error("CLI argument parsing error: {0}")]
    CliArgsError(#[from] clap::Error),

    #[error("GitHub API interaction error: {0}")]
    ApiErrorVariant(#[from] ApiError), // Renamed to avoid conflict with type ApiError

    #[error("Cache operation error: {0}")]
    CacheErrorVariant(#[from] CacheError), // Renamed to avoid conflict with type CacheError

    #[error("File parsing error: {0}")]
    ParseErrorVariant(#[from] ParseError), // Renamed to avoid conflict with type ParseError

    #[error("Action execution error: {0}")]
    ActionErrorVariant(#[from] ActionError), // Renamed to avoid conflict with type ActionError

    #[error("I/O error for path '{1}': {0}")]
    Io(#[source] std::io::Error, PathBuf), // Or IoError if Io is a type name

}

#[derive(Error, Debug)]
pub enum ApiError {
    #[error("Reqwest HTTP client error: {0}")]
    ReqwestError(#[from] reqwest::Error),

    #[error("GitHub API HTTP error (Status: {status}): {body}")]
    HttpError {
        status: reqwest::StatusCode,
        body: String,
    },

    #[error("Failed to deserialize API response: {0}")]
    DeserializationError(#[from] serde_json::Error),
}

#[derive(Error, Debug)]
pub enum CacheError {
    #[error("Failed to read/write cache file at '{1}': {0}")]
    Io(#[source] std::io::Error, PathBuf), // Or IoError

    #[error("Failed to serialize cache data: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Failed to deserialize cache data from '{1}': {0}")]
    Deserialization(#[source] serde_json::Error, PathBuf),
}

#[derive(Error, Debug)]
pub enum ParseError {
    #[error("YAML parsing error in file '{0}': {1}")]
    YamlError(String, #[source] serde_yaml::Error),

    #[error("Missing SPDX ID in license file: {0}")]
    MissingSpdxId(String),

    #[error("Regex error during parsing: {0}")]
    RegexError(#[from] regex::Error),
}

#[derive(Error, Debug)]
pub enum ActionError {
    #[error("License with SPDX ID '{0}' not found in cache.")]
    LicenseNotFound(String),

    #[error("Required data file '{0}' not found or failed to parse from cache.")]
    MissingData(String),

    #[error("Invalid input for action: {0}")]
    InvalidInput(String),

    #[error("Failed to perform file operation for '{1}': {0}")]
    FileOperation(#[source] std::io::Error, PathBuf),
}
