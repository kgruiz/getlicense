use reqwest::header::{ACCEPT, AUTHORIZATION};
use reqwest::Client;
use serde::de::DeserializeOwned;
use std::env;

use crate::error::ApiError;
// For specific deserialization
use crate::models::GitHubFile;
use crate::constants::{GITHUB_API_BASE_URL, GITHUB_API_VERSION_HEADER, APP_USER_AGENT};

fn GetHttpClient() -> Result<Client, reqwest::Error> {
    Client::builder()
        .user_agent(APP_USER_AGENT)
        .build()
}

async fn GetGithubApiGeneric<T: DeserializeOwned>(
    client: &Client,
    endpoint: &str,
) -> Result<T, ApiError> {
    let token = env::var("GITHUB_TOKEN").ok();
    let url = format!("{}{}", GITHUB_API_BASE_URL, endpoint);

    if unsafe { crate::VERBOSE } {
        eprintln!("API Request: GET {}", url);

        if token.is_some() {
            eprintln!("Using GITHUB_TOKEN.");
        }

    }

    let mut requestBuilder = client.get(&url).header(ACCEPT, GITHUB_API_VERSION_HEADER);

    if let Some(t) = token {
        requestBuilder = requestBuilder.header(AUTHORIZATION, format!("token {}", t));
    }

    let response = requestBuilder.send().await.map_err(ApiError::ReqwestError)?;

    if unsafe { crate::VERBOSE } {
        eprintln!("API Response Status: {}", response.status());
    }

    if !response.status().is_success() {
        let status = response.status();
        let errorText = response.text().await.unwrap_or_else(|_| "Failed to read error body".to_string());

        if status == reqwest::StatusCode::FORBIDDEN && errorText.contains("rate limit exceeded") {
             let rateLimitRemaining = env::var("X-RateLimit-Remaining").unwrap_or_else(|_| "N/A".to_string());
             eprintln!("[API] Rate limit likely exceeded. Remaining: {}", rateLimitRemaining);
        }

        return Err(ApiError::HttpError { status, body: errorText });

    }

    response.json::<T>().await.map_err(ApiError::ReqwestError)
}

pub async fn FetchGithubDirListing(
    owner: &str,
    repo: &str,
    path: &str,
    branch: &str,
) -> Result<Vec<GitHubFile>, ApiError> {
    let client = GetHttpClient().map_err(ApiError::ReqwestError)?;
    let endpoint = format!("/repos/{}/{}/contents/{}?ref={}", owner, repo, path, branch);

    return GetGithubApiGeneric::<Vec<GitHubFile>>(&client, &endpoint).await;

}

pub async fn FetchFileContent(downloadUrl: &str) -> Result<String, ApiError> {
    let client = GetHttpClient().map_err(ApiError::ReqwestError)?;

    if unsafe { crate::VERBOSE } {
        eprintln!("Fetching file content from: {}", downloadUrl);
    }

    let response = client
        .get(downloadUrl)
        // No need for GitHub API specific headers for raw download_url
        .send()
        .await
        .map_err(ApiError::ReqwestError)?;

    if unsafe { crate::VERBOSE } {
        eprintln!("File Content Response Status: {}", response.status());
    }

    if !response.status().is_success() {

        return Err(ApiError::HttpError {
            status: response.status(),
            body: response.text().await.unwrap_or_else(|_| "Failed to read error body".to_string()),
        });

    }

    response.text().await.map_err(ApiError::ReqwestError)
}
