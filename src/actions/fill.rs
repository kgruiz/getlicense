use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use chrono::Datelike;

use crate::models::Cache;
use crate::cli::{LicenseFillArgs, Cli as FullCliArgs};
use crate::parser;
use crate::display;
use crate::error::{AppError, ActionError};
use crate::constants::{USER_PLACEHOLDERS_KEY, CACHABLE_PLACEHOLDER_KEYS, CLI_ARG_TO_CACHE_KEY, RAW_PLACEHOLDER_TO_STANDARD_KEY};
use crate::cache;

pub async fn FillLicenseTemplateAction(
    cache: &mut Cache, // Mutable to update user_placeholders
    args: &LicenseFillArgs,
    cliAllArgs: &FullCliArgs, // To pass to display summary for context
) -> Result<bool, AppError> { // Returns true if user_placeholders cache was modified
    let spdxIdLower = args.license_id.to_lowercase();

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Filling license template for: {}", spdxIdLower);
    }

    let licenseEntry = cache::get_full_license_data_from_cache(&spdxIdLower, cache)?
        .ok_or_else(|| AppError::ActionError(ActionError::LicenseNotFound(spdxIdLower.clone())))?;

    let templateBody = &licenseEntry.file_content_cached;

    println!("\nUsing license: {} ({})",
        console::style(&licenseEntry.title).cyan().bold(),
        console::style(&licenseEntry.spdx_id).cyan()
    );


    let cachedPlaceholdersAtStart = cache.user_placeholders.clone();
    let mut userProvidedForCaching: HashMap<String, String> = HashMap::new();

    // Collect CLI args for cachable placeholders

    if let Some(name) = &args.fullname {
        userProvidedForCaching.insert(CLI_ARG_TO_CACHE_KEY["fullname"].to_string(), name.clone());
    }


    if let Some(proj) = &args.project {
        userProvidedForCaching.insert(CLI_ARG_TO_CACHE_KEY["project"].to_string(), proj.clone());
    }


    if let Some(mail) = &args.email {
        userProvidedForCaching.insert(CLI_ARG_TO_CACHE_KEY["email"].to_string(), mail.clone());
    }


    if let Some(url) = &args.projecturl {
        userProvidedForCaching.insert(CLI_ARG_TO_CACHE_KEY["projecturl"].to_string(), url.clone());
    }

    // --- Determine Final Replacements for Template Filling ---
    let mut finalTemplateReplacements: HashMap<String, String> = HashMap::new();

    // 1. Start with cached preferences (non-year)

    for keyStr in CACHABLE_PLACEHOLDER_KEYS {

        if let Some(val) = cachedPlaceholdersAtStart.get(*keyStr) {
            finalTemplateReplacements.insert(keyStr.to_string(), val.clone());
        }
    }

    // 2. Override with current CLI arguments (non-year)
    finalTemplateReplacements.extend(userProvidedForCaching.clone());

    // 3. Handle 'year' (default or CLI, not from cache)
    let currentYearStr = chrono::Local::now().year().to_string();
    let yearToUse = args.year.as_ref().unwrap_or(¤tYearStr);
    finalTemplateReplacements.insert("year".to_string(), yearToUse.clone());
    // Ensure related keys like 'yyyy' also get this year value for filling
    // The FillLicenseTemplate function should handle mapping "year" to "[yyyy]" etc.
    // based on RAW_PLACEHOLDER_TO_STANDARD_KEY.

    // For summary: user_provided_for_filling_summary includes explicit CLI args + year used
    let mut userProvidedForFillingSummary = userProvidedForCaching.clone();
    userProvidedForFillingSummary.insert("year".to_string(), yearToUse.clone());


    let filledLicenseBody = parser::fill_license_template_body(templateBody, &finalTemplateReplacements);

    let outputPath = args.output.clone().unwrap_or_else(|| PathBuf::from("LICENSE"));

    if let Some(parent) = outputPath.parent() {
        fs::create_dir_all(parent).map_err(|e| AppError::Io(e, parent.to_path_buf()))?;
    }
    // Add newline at end
    fs::write(&outputPath, filledLicenseBody.clone() + "\n")
        .map_err(|e| AppError::Io(e, outputPath.clone()))?;

    let mut placeholderCacheModified = false;

    if !userProvidedForCaching.is_empty() {
        // Merge new CLI args into the main cache's user_placeholders
        cache.user_placeholders.extend(userProvidedForCaching);
        placeholderCacheModified = true;

        if unsafe { crate::main::VERBOSE } {
            eprintln!("[Action] Updated saved placeholder preferences with current CLI arguments.");
        }
    }

    display::display_license_summary_after_write(
        &licenseEntry,
        // Pass the whole cache for access to fields.yml etc.
        &cache,
        &outputPath,
        &userProvidedForFillingSummary,
        &cachedPlaceholdersAtStart,
        &filledLicenseBody,
        // Pass all CLI args for context
        cliAllArgs,
    );


    Ok(placeholderCacheModified)
}