use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use crate::models::Cache;
use crate::cli::{LicenseFillArgs, Cli as FullCliArgs};
use crate::parser;
use crate::display;
use crate::error::{AppError, ActionError};
use crate::constants::{CACHABLE_PLACEHOLDER_KEYS, CLI_ARG_TO_CACHE_KEY_TUPLES};
use chrono::Datelike;
use colored::*;


pub async fn FillLicenseTemplateAction(
    cache: &mut Cache,
    args: &LicenseFillArgs,
    cliAllArgs: &FullCliArgs,
) -> Result<bool, AppError> {

    let spdxIdLower = args.licenseId.to_lowercase();


    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Filling license template for: {}", spdxIdLower);
    }

    let licenseEntry = cache.licenses.get(&spdxIdLower)
        .ok_or_else(|| AppError::ActionErrorVariant(ActionError::LicenseNotFound(spdxIdLower.clone())))?;

    let templateBody = &licenseEntry.fileContentCached;

    println!("\nUsing license: {} ({})",
        licenseEntry.title.cyan().bold(),
        licenseEntry.spdxId.cyan()
    );

    let cachedPlaceholdersAtStart = cache.userPlaceholders.clone();
    let mut userProvidedForCaching: HashMap<String, String> = HashMap::new();

    // Collect CLI args for cachable placeholders
    let cliArgToCacheKeyMap: HashMap<&str, &str> = CLI_ARG_TO_CACHE_KEY_TUPLES.iter().cloned().collect();


    if let Some(name) = &args.fullname {

        if let Some(key) = cliArgToCacheKeyMap.get("fullname") {
             userProvidedForCaching.insert(key.to_string(), name.clone());
        }

    }


    if let Some(proj) = &args.project {

         if let Some(key) = cliArgToCacheKeyMap.get("project") {
            userProvidedForCaching.insert(key.to_string(), proj.clone());
        }

    }


    if let Some(mail) = &args.email {

        if let Some(key) = cliArgToCacheKeyMap.get("email") {
            userProvidedForCaching.insert(key.to_string(), mail.clone());
        }

    }


    if let Some(url) = &args.projecturl {

        if let Some(key) = cliArgToCacheKeyMap.get("projecturl") {
            userProvidedForCaching.insert(key.to_string(), url.clone());
        }

    }

    // --- Determine Final Replacements for Template Filling ---
    let mut finalTemplateReplacements: HashMap<String, String> = HashMap::new();

    // 1. Start with cached preferences (non-year)

    for keyStr in CACHABLE_PLACEHOLDER_KEYS.iter() { // CACHABLE_PLACEHOLDER_KEYS is an array of &str

        if let Some(val) = cachedPlaceholdersAtStart.get(*keyStr) {
            finalTemplateReplacements.insert(keyStr.to_string(), val.clone());
        }

    }

    // 2. Override with current CLI arguments (non-year)
    finalTemplateReplacements.extend(userProvidedForCaching.clone());

    // 3. Handle 'year' (default or CLI, not from cache)
    let currentYearStr = chrono::Local::now().year().to_string();
    let year_to_use = args.year.as_ref().unwrap_or(&currentYearStr);
    finalTemplateReplacements.insert("year".to_string(), year_to_use.clone());

    // For summary: user_provided_for_filling_summary includes explicit CLI args + year used
    let mut userProvidedForFillingSummary = userProvidedForCaching.clone();
    userProvidedForFillingSummary.insert("year".to_string(), year_to_use.clone());

    // Pass the extracted placeholders from the license entry
    let filledLicenseBody = parser::FillLicenseTemplateBody(
        templateBody,
        &finalTemplateReplacements,
        &licenseEntry.placeholdersInBody
    );

    let outputPath = args.output.clone().unwrap_or_else(|| PathBuf::from("LICENSE"));


    if let Some(parent) = outputPath.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::Io(e, parent.to_path_buf()))?;
    }

    fs::write(&outputPath, filledLicenseBody.clone() + "\n")
        .map_err(|e| AppError::Io(e, outputPath.clone()))?;

    let mut placeholderCacheModified = false;


    if !userProvidedForCaching.is_empty() {
        cache.userPlaceholders.extend(userProvidedForCaching);
        placeholderCacheModified = true;

        if unsafe { crate::VERBOSE } {
            eprintln!("[Action] Updated saved placeholder preferences with current CLI arguments.");
        }

    }

    // Pass the whole cache for access to fields.yml etc. for summary display
    // Pass all CLI args for context for the summary display    
    display::DisplayLicenseSummaryAfterWrite(
        &licenseEntry,
        &cache,
        &outputPath,
        &userProvidedForFillingSummary,
        &cachedPlaceholdersAtStart,
        &filledLicenseBody,
        cliAllArgs,
    );


    Ok(placeholderCacheModified)
}
