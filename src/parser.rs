use regex::Regex;
use serde::de::DeserializeOwned;
use std::collections::{HashMap, HashSet};

use crate::models::{FrontMatter, InfoComponents, ParsedRules, RuleDetail, RulesDataContent, RuleSource};
use crate::error::ParseError;
use crate::constants::RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES;

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

    let mut frontMatter: FrontMatter =

        if let Some(fmStr) = fmStrOpt {
            serde_yaml::from_str(&fmStr).map_err(|e| ParseError::YamlError(filename.to_string(), e))?
        }

        else {

            if unsafe { crate::VERBOSE } {
                eprintln!("[Parse] No YAML front matter found in {}", filename);
            }

            FrontMatter::default()
        };

    let spdxId = match frontMatter.spdxId.as_deref() {
        Some(id) if !id.trim().is_empty() => id.trim().to_string(),
        _ => GuessSpdxFromFilename(filename)
            .ok_or_else(|| ParseError::MissingSpdxId(filename.to_string()))?,
    };

    if frontMatter.spdxId.is_none() || frontMatter.spdxId.as_deref().unwrap_or("").trim().is_empty() {
        frontMatter.spdxId = Some(spdxId.clone());
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
    let re = Regex::new(r"^[A-Za-z0-9.\-+]+$").unwrap();

    if re.is_match(namePart) {
        Some(namePart.to_string())
    }

    else {

        if unsafe { crate::VERBOSE } {
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

// Renamed as not used directly yet
pub fn _ParseDataFileToStruct<T: DeserializeOwned>(
    filename: &str,
    fileContent: &str,
) -> Result<T, ParseError> {

    serde_yaml::from_str(fileContent).map_err(|e| ParseError::YamlError(filename.to_string(), e))
}

pub fn FindPlaceholdersInBody(body: &str) -> Vec<String> {
    let re = Regex::new(r"\[([^\]]+)\]").unwrap();
    let mut placeholders = HashSet::new();

    for cap in re.captures_iter(body) {
        placeholders.insert(cap[0].to_string());
    }

    let mut sortedPlaceholders: Vec<String> = placeholders.into_iter().collect();
    sortedPlaceholders.sort();

    sortedPlaceholders
}

fn BuildParsedRulesCategory(
    fmRuleTags: &[String],
    categoryNameInRulesData: &str,
    allRulesData: &Option<RulesDataContent>,
) -> Vec<RuleDetail> {
    let mut parsedDetails = Vec::new();

    if let Some(rulesContent) = allRulesData {
        let sourceRulesList = match categoryNameInRulesData {
            "permissions" => &rulesContent.permissions,
            "conditions" => &rulesContent.conditions,
            "limitations" => &rulesContent.limitations,
            _ => return vec![],
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
            }

            else {
                parsedDetails.push(RuleDetail {
                    tag: tag.clone(),
                    label: tag.clone(),
                    description: "Description not found in rules.yml.".to_string(),
                });
            }

        }

        parsedDetails.sort_by(|a, b| a.label.cmp(&b.label));
    }

    else {

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
        howToApplyText: fm.how.clone(),
        noteText: fm.note.clone(),
        usingInfo: fm.using.clone(),
        parsedRules: ParsedRules {
            permissions: BuildParsedRulesCategory(&fm.permissions, "permissions", allRulesData),
            conditions: BuildParsedRulesCategory(&fm.conditions, "conditions", allRulesData),
            limitations: BuildParsedRulesCategory(&fm.limitations, "limitations", allRulesData),
        },
    }
}

// replacements: Standard keys: "fullname", "year", etc.
// placeholdersAsFoundInBody: e.g. "[year]", "[fullname]", "[name of copyright owner]"
pub fn FillLicenseTemplateBody(
    templateBody: &str,
    replacements: &HashMap<String, String>,
    placeholdersAsFoundInBody: &[String],
) -> String {
    let mut filledBody = templateBody.to_string();
    let rawToStdMap: HashMap<&str, &str> = RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES.iter().cloned().collect();

    for phInBodyWithBrackets in placeholdersAsFoundInBody {
        // ph_in_body_with_brackets is like "[year]" or "[name of copyright owner]"
        let phTextNoBrackets = phInBodyWithBrackets.trim_matches(|c| c == '[' || c == ']');
        let phTextNoBracketsLower = phTextNoBrackets.to_lowercase();

        // Find the standard key for this placeholder

        if let Some(standardKey) = rawToStdMap.get(phTextNoBracketsLower.as_str()) {

            // Check if a replacement value is provided for this standard key

            if let Some(valueToInsert) = replacements.get(*standardKey) {
                filledBody = filledBody.replace(phInBodyWithBrackets, valueToInsert);
            }

        }

    }

    filledBody
}


#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn TestSplitFrontMatterBasic() {
        let content = "---\ntitle: MIT\n---\nBody text";
        let (fm, body) = SplitFrontMatterAndBody(content);
        assert_eq!(fm, Some("title: MIT".to_string()));
        assert_eq!(body, "Body text");
    }

    #[test]
    fn TestSplitNoFrontMatter() {
        let content = "Body text only";
        let (fm, body) = SplitFrontMatterAndBody(content);
        assert_eq!(fm, None);
        assert_eq!(body, "Body text only");
    }

    #[test]
    fn TestFindPlaceholders() {
        let body = "Copyright [year] by [fullname]. Project: [project].";
        let expected = vec!["[fullname]", "[project]", "[year]"];
        let mut actual = FindPlaceholdersInBody(body);
        // find_placeholders_in_body already sorts
        actual.sort();
        assert_eq!(actual, expected);
    }

    #[test]
    fn TestFillLicenseTemplateBodySimple() {
        let template = "License for [project] by [fullname] in [year].";
        let mut replacements = HashMap::new();
        replacements.insert("project".to_string(), "MyLib".to_string());
        replacements.insert("fullname".to_string(), "John Doe".to_string());
        replacements.insert("year".to_string(), "2024".to_string());

        // These are the placeholders as they would be extracted by find_placeholders_in_body
        let placeholdersInTemplate = vec![
            "[project]".to_string(),
            "[fullname]".to_string(),
            "[year]".to_string()
        ];

        let filled = FillLicenseTemplateBody(template, &replacements, &placeholdersInTemplate);
        assert_eq!(filled, "License for MyLib by John Doe in 2024.");
    }

    #[test]
    fn TestFillLicenseTemplateBodyMappedPlaceholders() {
        let template = "Copyright [yyyy] by [name of copyright owner].";
        let mut replacements = HashMap::new();
        // Standard key is "year"
        replacements.insert("year".to_string(), "2023".to_string());
        // Standard key is "fullname"
        replacements.insert("fullname".to_string(), "Acme Corp".to_string());

        let placeholdersInTemplate = vec![
            "[yyyy]".to_string(),
            "[name of copyright owner]".to_string()
        ];

        let filled = FillLicenseTemplateBody(template, &replacements, &placeholdersInTemplate);
        assert_eq!(filled, "Copyright 2023 by Acme Corp.");
    }

     #[test]
    fn TestFillLicenseTemplateBodyUnfilledPlaceholders() {
        let template = "Project: [project], Owner: [fullname], Contact: [email].";
        let mut replacements = HashMap::new();
        replacements.insert("project".to_string(), "RustApp".to_string());
        // "fullname" and "email" are not provided

        let placeholdersInTemplate = vec![
            "[project]".to_string(),
            "[fullname]".to_string(),
            "[email]".to_string()
        ];

        let filled = FillLicenseTemplateBody(template, &replacements, &placeholdersInTemplate);
        // Unfilled placeholders should remain as they are
        assert_eq!(filled, "Project: RustApp, Owner: [fullname], Contact: [email].");
    }
}
