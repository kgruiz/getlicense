use std::path::PathBuf;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum AppError {
    #[error("CLI argument parsing error: {0}")]
    cliArgsError(#[from] clap::Error),

    #[error("GitHub API interaction error: {0}")]
    apiError(#[from] ApiError),

    #[error("Cache operation error: {0}")]
    cacheError(#[from] CacheError),

    #[error("File parsing error: {0}")]
    parseError(#[from] ParseError),

    #[error("Action execution error: {0}")]
    actionError(#[from] ActionError),

    #[error("I/O error for path '{1}': {0}")]
    io(#[source] std::io::Error, PathBuf),

    #[error("Configuration error: {0}")]
    configError(String),

    #[error("No action specified by the user.")]
    noActionSpecified,
}

#[derive(Error, Debug)]
pub enum ApiError {
    #[error("Reqwest HTTP client error: {0}")]
    reqwestError(#[from] reqwest::Error),

    #[error("GitHub API HTTP error (Status: {status}): {body}")]
    httpError {
        status: reqwest::StatusCode,
        body: String,
    },

    #[error("Failed to deserialize API response: {0}")]
    deserializationError(#[from] serde_json::Error),

    #[error("API endpoint not found or invalid: {0}")]
    endpointNotFound(String),
}

#[derive(Error, Debug)]
pub enum CacheError {
    #[error("Failed to read/write cache file at '{1}': {0}")]
    io(#[source] std::io::Error, PathBuf),

    #[error("Failed to serialize cache data: {0}")]
    serialization(#[from] serde_json::Error),

    #[error("Failed to deserialize cache data from '{1}': {0}")]
    deserialization(#[source] serde_json::Error, PathBuf),

    #[error("Cache entry not found for key: {0}")]
    entryNotFound(String),

    #[error("Cache data is in an inconsistent or invalid state: {0}")]
    invalidState(String),
}

#[derive(Error, Debug)]
pub enum ParseError {
    #[error("YAML parsing error in file '{0}': {1}")]
    yamlError(String, #[source] serde_yaml::Error),

    #[error("Missing SPDX ID in license file: {0}")]
    missingSpdxId(String),

    #[error("Front matter parsing failed for file: {0}")]
    frontMatterError(String),

    #[error("Regex error during parsing: {0}")]
    regexError(#[from] regex::Error),
}

#[derive(Error, Debug)]
pub enum ActionError {
    #[error("License with SPDX ID '{0}' not found in cache.")]
    licenseNotFound(String),

    #[error("Required data file '{0}' not found or failed to parse from cache.")]
    missingData(String),

    #[error("Invalid input for action: {0}")]
    invalidInput(String),

    #[error("Failed to perform file operation for '{1}': {0}")]
    fileOperation(#[source] std::io::Error, PathBuf),

    #[error("An unexpected error occurred during action: {0}")]
    other(String),
}