use std::collections::HashSet;
use crate::models::{Cache, LicenseEntry, RulesDataContent};
use crate::display;
use crate::error::{AppError, ActionError};

pub async fn FindMatchingLicenses(
    cache: &Cache,
    requireTagsOpt: Option<Vec<String>>,
    disallowTagsOpt: Option<Vec<String>>,
) -> Result<(), AppError> {
    let requireTags = requireTagsOpt.unwrap_or_default();
    let disallowTags = disallowTagsOpt.unwrap_or_default();


    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Finding licenses. Require: {:?}, Disallow: {:?}", requireTags, disallowTags);
    }


    if requireTags.is_empty() && disallowTags.is_empty() {

        return Err(AppError::ActionErrorVariant(ActionError::InvalidInput(
            "Please provide at least one --require or --disallow tag for finding licenses.".to_string(),
        )));

    }


    let rulesDataContent: RulesDataContent = cache.dataFiles // dataFiles is correct
        .get(crate::constants::RULES_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok())
        .ok_or_else(|| AppError::ActionErrorVariant(ActionError::MissingData( // Corrected this line based on other similar changes
            "rules.yml data not found in cache. Cannot validate find tags.".to_string()
        )))?;

    let mut allValidTags = HashSet::new();


    for ruleList in [&rulesDataContent.permissions, &rulesDataContent.conditions, &rulesDataContent.limitations].iter() {

        for ruleSource in *ruleList {
            allValidTags.insert(ruleSource.tag.clone());
        }

    }


    let invalidRequire: Vec<_> = requireTags.iter().filter(|t| !allValidTags.contains(*t)).cloned().collect();
    let invalidDisallow: Vec<_> = disallowTags.iter().filter(|t| !allValidTags.contains(*t)).cloned().collect();


    if !invalidRequire.is_empty() || !invalidDisallow.is_empty() {
        let mut errMsg = "Invalid rule tags provided:".to_string();


        if !invalidRequire.is_empty() {
            errMsg.push_str(&format!("\n  Invalid --require tags: {}", invalidRequire.join(", ")));
        }


        if !invalidDisallow.is_empty() {
            errMsg.push_str(&format!("\n  Invalid --disallow tags: {}", invalidDisallow.join(", ")));
        }


        return Err(AppError::ActionErrorVariant(ActionError::InvalidInput(errMsg)));

    }


    let mut matches: Vec<&LicenseEntry> = Vec::new();


    for licenseEntry in cache.licenses.values() {
        // The raw tags are directly available in LicenseEntry
        let mut licenseRules = HashSet::new();
        licenseRules.extend(licenseEntry.permissions.iter().cloned());
        licenseRules.extend(licenseEntry.conditions.iter().cloned());
        licenseRules.extend(licenseEntry.limitations.iter().cloned());

        let meetsRequire = requireTags.iter().all(|tag| licenseRules.contains(tag));
        let meetsDisallow = !disallowTags.iter().any(|tag| licenseRules.contains(tag));


        if meetsRequire && meetsDisallow {
            matches.push(licenseEntry);
        }

    }

    // Sort matches by SPDX ID for consistent output
    matches.sort_by_key(|entry| &entry.spdxId);

    display::PrintFindResults(&matches, &requireTags, &disallowTags);

    Ok(())
}
