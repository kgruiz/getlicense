use regex::Regex;
use serde::de::DeserializeOwned;
use std::collections::HashSet;
use std::collections::HashMap;

use crate::models::{FrontMatter, InfoComponents, ParsedRules, RuleDetail, RulesDataContent, RuleSource};
use crate::error::ParseError;
// For standardizing during parsing if needed
use crate::constants::RAW_PLACEHOLDER_TO_STANDARD_KEY;

fn SplitFrontMatterAndBody(fileContent: &str) -> (Option<String>, String) {

    if fileContent.starts_with("---") {

        if let Some(endFmIdx) = fileContent.get(3..).and_then(|s| s.find("---")) {
            let fmStr = fileContent[3..(3 + endFmIdx)].trim().to_string();
            let bodyStr = fileContent[(3 + endFmIdx + 3)..].trim().to_string();

            return (Some(fmStr), bodyStr);
        }

    }

    (None, fileContent.trim().to_string())
}

pub fn ParseLicenseFile(
    filename: &str,
    fileContent: &str,
) -> Result<(String, FrontMatter, String), ParseError> {
    let (fmStrOpt, body) = SplitFrontMatterAndBody(fileContent);

    let mut frontMatter: FrontMatter = if let Some(fmStr) = fmStrOpt {

        serde_yaml::from_str(&fmStr).map_err(|e| ParseError::YamlError(filename.to_string(), e))?

    } else {

        if unsafe { crate::main::VERBOSE } {

            eprintln!("[Parse] No YAML front matter found in {}", filename);

        }

        FrontMatter::default()

    };

    let spdxId = match frontMatter.spdx_id.as_deref() {

        Some(id) if !id.trim().is_empty() => id.trim().to_string(),
        _ => GuessSpdxFromFilename(filename)
            .ok_or_else(|| ParseError::MissingSpdxId(filename.to_string()))?,

    };

    if frontMatter.spdx_id.is_none() || frontMatter.spdx_id.as_deref().unwrap_or("").trim().is_empty() {

        frontMatter.spdx_id = Some(spdxId.clone());

    }

    if frontMatter.title.is_none() || frontMatter.title.as_deref().unwrap_or("").trim().is_empty() {

        frontMatter.title = Some(spdxId.clone());

    }


    Ok((spdxId, frontMatter, body))
}

pub fn GuessSpdxFromFilename(filename: &str) -> Option<String> {
    let namePart = std::path::Path::new(filename)
        .file_stem()
        .and_then(|os_str| os_str.to_str())?;

    // This unwrap is considered safe as the regex pattern is static and valid.
    let re = Regex::new(r"^[A-Za-z0-9.\-+]+$").unwrap();

    if re.is_match(namePart) {

        Some(namePart.to_string())

    } else {

        if unsafe { crate::main::VERBOSE } {

            eprintln!("[Parse] Filename stem '{}' from '{}' does not look like an SPDX ID.", namePart, filename);

        }

        None

    }
}

pub fn ParseDataFileToValue(
    filename: &str,
    fileContent: &str,
) -> Result<serde_yaml::Value, ParseError> {
    serde_yaml::from_str(fileContent).map_err(|e| ParseError::YamlError(filename.to_string(), e))
}

pub fn ParseDataFileToStruct<T: DeserializeOwned>(
    filename: &str,
    fileContent: &str,
) -> Result<T, ParseError> {
    serde_yaml::from_str(fileContent).map_err(|e| ParseError::YamlError(filename.to_string(), e))
}


pub fn FindPlaceholdersInBody(body: &str) -> Vec<String> {
    // This unwrap is considered safe as the regex pattern is static and valid.
    let re = Regex::new(r"\[([^\]]+)\]").unwrap();
    let mut placeholders = HashSet::new();


    for cap in re.captures_iter(body) {

        // cap[0] is the full match, e.g., "[fullname]"
        placeholders.insert(cap[0].to_string());

    }


    let mut sortedPlaceholders: Vec<String> = placeholders.into_iter().collect();
    sortedPlaceholders.sort();

    sortedPlaceholders
}

fn BuildParsedRulesCategory(
    fmRuleTags: &[String],
    categoryNameInRulesData: &str, // e.g., "permissions"
    allRulesData: &Option<RulesDataContent>,
) -> Vec<RuleDetail> {
    let mut parsedDetails = Vec::new();


    if let Some(rulesContent) = allRulesData {

        let sourceRulesList = match categoryNameInRulesData {

            "permissions" => &rulesContent.permissions,
            "conditions" => &rulesContent.conditions,
            "limitations" => &rulesContent.limitations,
            // Unknown category
            _ => {

                return vec![];

            }

        };

        let rulesMap: HashMap<&String, &RuleSource> =
            sourceRulesList.iter().map(|r| (&r.tag, r)).collect();


        for tag in fmRuleTags {

            if let Some(ruleSource) = rulesMap.get(tag) {

                parsedDetails.push(RuleDetail {
                    tag: ruleSource.tag.clone(),
                    label: ruleSource.label.clone(),
                    description: ruleSource.description.clone(),
                });

            } else {

                // Rule tag found in license front matter but not in rules.yml
                parsedDetails.push(RuleDetail {
                    tag: tag.clone(),
                    // Fallback label
                    label: tag.clone(),
                    description: "Description not found in rules.yml.".to_string(),
                });

            }

        }

        parsedDetails.sort_by(|a, b| a.label.cmp(&b.label));

    } else {
        // rules.yml data not available

        for tag in fmRuleTags {

            parsedDetails.push(RuleDetail {
                tag: tag.clone(),
                label: tag.clone(),
                description: "Rules data (rules.yml) not available for full description.".to_string(),
            });

        }

    }

    parsedDetails
}


pub fn BuildInfoComponents(
    fm: &FrontMatter,
    allRulesData: &Option<RulesDataContent>,
) -> InfoComponents {
    InfoComponents {
        how_to_apply_text: fm.how.clone(),
        note_text: fm.note.clone(),
        using_info: fm.using.clone(),
        parsed_rules: ParsedRules {
            permissions: BuildParsedRulesCategory(&fm.permissions, "permissions", allRulesData),
            conditions: BuildParsedRulesCategory(&fm.conditions, "conditions", allRulesData),
            limitations: BuildParsedRulesCategory(&fm.limitations, "limitations", allRulesData),
        },
    }
}