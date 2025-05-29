use indicatif::{ProgressBar, ProgressStyle};
use serde_json;
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::sync::atomic::Ordering;

use crate::constants::{
    BRANCH_CONST, DATA_PATH_STR, LICENSES_PATH_STR, OWNER_CONST, REPO_CONST, RULES_YML_KEY,
};
use crate::error::CacheError;
use crate::models::{Cache, DataFileEntry, GitHubFile, LicenseEntry, RulesDataContent};

pub fn LoadCache(cachePath: &Path) -> Result<Cache, CacheError> {
    if !cachePath.exists() {
        // exists() is fine
        if crate::VERBOSE.load(Ordering::SeqCst) {
            eprintln!(
                "[Cache] Cache file not found at {:?}. Starting with empty cache.",
                cachePath
            );
        }

        return Ok(Cache::default());
    }

    let content =
        fs::read_to_string(cachePath).map_err(|e| CacheError::Io(e, cachePath.to_path_buf()))?;

    if content.trim().is_empty() {
        if crate::VERBOSE.load(Ordering::SeqCst) {
            eprintln!(
                "[Cache] Cache file at {:?} is empty. Starting fresh.",
                cachePath
            );
        }

        return Ok(Cache::default());
    }

    serde_json::from_str(&content)
        .map_err(|e| CacheError::Deserialization(e, cachePath.to_path_buf()))
}

pub fn SaveCache(cachePath: &Path, cacheData: &Cache) -> Result<(), CacheError> {
    if let Some(parent) = cachePath.parent() {
        fs::create_dir_all(parent).map_err(|e| CacheError::Io(e, parent.to_path_buf()))?;
    }
    let content = serde_json::to_string_pretty(cacheData).map_err(CacheError::Serialization)?;
    fs::write(cachePath, content).map_err(|e| CacheError::Io(e, cachePath.to_path_buf()))?;

    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!("[Cache] Cache saved to {:?}", cachePath);
    }

    Ok(())
}

fn NewProgressBar(totalItems: u64, message: &str) -> ProgressBar {
    let pb = ProgressBar::new(totalItems);
    pb.set_style(
        ProgressStyle::default_bar()
            .template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] {pos}/{len} ({percent}%) {msg}")
            .unwrap_or_else(|_| ProgressStyle::default_bar())
            .progress_chars("#>-"),
    );
    pb.set_message(message.to_string());

    pb
}

pub async fn UpdateAndLoadLicenseCache(
    cachePath: &Path,
    forceRefresh: bool,
) -> Result<(Cache, bool), CacheError> {
    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!(
            "[Cache] Updating and loading license cache from {:?}...",
            cachePath
        );
    }

    let mut currentCache = if forceRefresh {
        if crate::VERBOSE.load(Ordering::SeqCst) {
            eprintln!(
                "[Cache] Force refresh enabled. Ignoring existing cache content for fetching."
            );
        }
        Cache::default()
    } else {
        LoadCache(cachePath).unwrap_or_else(|err| {
            if crate::VERBOSE.load(Ordering::SeqCst) {
                eprintln!(
                    "[Cache] Warning: Failed to load cache ({:?}), starting fresh: {}",
                    cachePath, err
                );
            }
            Cache::default()
        })
    };

    let userPlaceholdersBackup = if !forceRefresh {
        currentCache.userPlaceholders.clone()
    } else {
        let diskCacheForPlaceholders = LoadCache(cachePath).unwrap_or_default();
        diskCacheForPlaceholders.userPlaceholders
    };

    let mut cacheUpdatedByFetch = false;
    let mut newLicensesCache: HashMap<String, LicenseEntry> = HashMap::new();
    let mut newDataFilesCache: HashMap<String, DataFileEntry> = HashMap::new();

    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!("[Cache] Checking _data files...");
    }

    match crate::api::FetchGithubDirListing(OWNER_CONST, REPO_CONST, DATA_PATH_STR, BRANCH_CONST)
        .await
    {
        Ok(ghDataFiles) => {
            for ghFileInfo in ghDataFiles
                .iter()
                .filter(|f| f.fileType == "file" && f.name.ends_with(".yml"))
            {
                let cacheKey = format!("data:{}", ghFileInfo.name);
                let existingEntry = currentCache.dataFiles.get(&cacheKey);

                if forceRefresh || existingEntry.map_or(true, |e| e.sha != ghFileInfo.sha) {
                    if crate::VERBOSE.load(Ordering::SeqCst) {
                        eprintln!("[Cache] Fetching data file: {}", ghFileInfo.name);
                    }

                    if let Some(url) = &ghFileInfo.downloadUrl {
                        match crate::api::FetchFileContent(url).await {
                            Ok(content) => {
                                if crate::VERBOSE.load(Ordering::SeqCst) {
                                    eprintln!(
                                        "[Cache] Successfully fetched data file: {}",
                                        ghFileInfo.name
                                    );
                                }
                                match crate::parser::ParseDataFileToValue(
                                    &ghFileInfo.name,
                                    &content,
                                ) {
                                    Ok(parsed_content) => {
                                        newDataFilesCache.insert(
                                            cacheKey.clone(),
                                            DataFileEntry {
                                                sha: ghFileInfo.sha.clone(),
                                                content: parsed_content,
                                            },
                                        );
                                        cacheUpdatedByFetch = true;
                                        if crate::VERBOSE.load(Ordering::SeqCst) {
                                            eprintln!(
                                                "[Cache] Parsed and cached data file: {}",
                                                ghFileInfo.name
                                            );
                                        }
                                    }
                                    Err(e) => eprintln!(
                                        "[Cache] Error parsing data file {}: {}",
                                        ghFileInfo.name, e
                                    ),
                                }
                            }
                            Err(e) => eprintln!(
                                "[Cache] Error fetching content for data file {}: {}",
                                ghFileInfo.name, e
                            ),
                        }
                    }
                } else if let Some(entry) = existingEntry {
                    if crate::VERBOSE.load(Ordering::SeqCst) {
                        eprintln!("[Cache] Using cached data file: {}", ghFileInfo.name);
                    }
                    newDataFilesCache.insert(cacheKey.clone(), entry.clone());
                }
            }
        }
        Err(e) => {
            eprintln!("[Cache] Warning: Could not fetch _data directory listing: {}. Using cached data files if available.", e);
            newDataFilesCache.extend(currentCache.dataFiles.clone());
        }
    }

    let rulesDataContent: Option<RulesDataContent> = newDataFilesCache
        .get(RULES_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!("[Cache] Checking _licenses files...");
    }

    match crate::api::FetchGithubDirListing(
        OWNER_CONST,
        REPO_CONST,
        LICENSES_PATH_STR,
        BRANCH_CONST,
    )
    .await
    {
        Ok(ghLicenseFilesInfo) => {
            let filesToProcess: Vec<&GitHubFile> = ghLicenseFilesInfo
                .iter()
                .filter(|f| f.fileType == "file" && f.name.ends_with(".txt"))
                .collect();

            if !filesToProcess.is_empty() {
                let pb = NewProgressBar(filesToProcess.len() as u64, "Syncing licenses");

                for ghFileInfo in filesToProcess {
                    pb.set_message(format!("Processing {}", ghFileInfo.name));

                    let mut existingEntryKey: Option<String> = None;
                    let mut existingEntrySha: Option<String> = None;

                    for (key, entry) in &currentCache.licenses {
                        if entry.filename == ghFileInfo.name {
                            existingEntryKey = Some(key.clone());
                            existingEntrySha = Some(entry.sha.clone());
                            break;
                        }
                    }

                    if forceRefresh || existingEntrySha.map_or(true, |s| s != ghFileInfo.sha) {
                        if crate::VERBOSE.load(Ordering::SeqCst) {
                            eprintln!("[Cache] Fetching license file: {}", ghFileInfo.name);
                        }

                        if let Some(url) = &ghFileInfo.downloadUrl {
                            match crate::api::FetchFileContent(url).await {
                                Ok(content) => {
                                    if crate::VERBOSE.load(Ordering::SeqCst) {
                                        eprintln!("[Cache] Successfully fetched license file: {}", ghFileInfo.name);
                                    }
                                    match crate::parser::ParseLicenseFile(
                                        &ghFileInfo.name,
                                        &content,
                                    ) {
                                        Ok((spdxId, fm, body)) => {
                                            let placeholders =
                                                crate::parser::FindPlaceholdersInBody(&body);
                                            let infoComponents = crate::parser::BuildInfoComponents(
                                                &fm,
                                                &rulesDataContent,
                                            );
                                            let licenseEntry = LicenseEntry {
                                                spdxId: spdxId.clone(),
                                                title: fm.title.unwrap_or_else(|| spdxId.clone()),
                                                nickname: fm.nickname,
                                                description: fm.description,
                                                filename: ghFileInfo.name.clone(),
                                                sha: ghFileInfo.sha.clone(),
                                                permissions: fm.permissions,
                                                conditions: fm.conditions,
                                                limitations: fm.limitations,
                                                fileContentCached: content,
                                                placeholdersInBody: placeholders,
                                                infoComponents: infoComponents,
                                            };
                                            newLicensesCache
                                                .insert(spdxId.to_lowercase(), licenseEntry);
                                            cacheUpdatedByFetch = true;
                                            if crate::VERBOSE.load(Ordering::SeqCst) {
                                                eprintln!(
                                                    "[Cache] Parsed and cached license file: {} (SPDX: {})",
                                                    ghFileInfo.name, spdxId
                                                );
                                            }
                                        }
                                        Err(e) => eprintln!(
                                            "[Cache] Error parsing license file {}: {}",
                                            ghFileInfo.name, e
                                        ),
                                    }
                                }
                                Err(e) => eprintln!(
                                    "[Cache] Error fetching content for license {}: {}",
                                    ghFileInfo.name, e
                                ),
                            }
                        }
                    } else if let Some(key) = existingEntryKey {
                        if let Some(entry) = currentCache.licenses.get(&key) {
                            if crate::VERBOSE.load(Ordering::SeqCst) {
                                eprintln!("[Cache] Using cached license file: {}", ghFileInfo.name);
                            }
                            newLicensesCache.insert(entry.spdxId.to_lowercase(), entry.clone());
                        }
                    }
                    pb.inc(1);
                }

                pb.finish_with_message("License sync complete.");
            } else {
                if crate::VERBOSE.load(Ordering::SeqCst) {
                    eprintln!("[Cache] No .txt files found in _licenses directory on GitHub.");
                }
            }
        }
        Err(e) => {
            eprintln!("[Cache] Warning: Could not fetch _licenses directory listing: {}. Using cached licenses if available.", e);
            newLicensesCache.extend(currentCache.licenses.clone());
        }
    }

    currentCache.licenses = newLicensesCache;
    currentCache.dataFiles = newDataFilesCache;
    currentCache.userPlaceholders = userPlaceholdersBackup;

    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!(
            "[Cache] Cache now has {} licenses and {} data files (updated_by_fetch={})",
            currentCache.licenses.len(),
            currentCache.dataFiles.len(),
            cacheUpdatedByFetch
        );
    }

    if currentCache.licenses.is_empty() && currentCache.dataFiles.is_empty() && !cacheUpdatedByFetch {
        eprintln!("Warning: cache is still empty. Check network access or parsing errors.");
    }

    if !cacheUpdatedByFetch && !forceRefresh && crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!("[Cache] Cache is up-to-date regarding remote files.");
    }

    Ok((currentCache, cacheUpdatedByFetch))
}
