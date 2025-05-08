use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct Cache {
    // Ensures licenses field exists even if missing in JSON
    #[serde(default)]
    // Key: lowercase SPDX ID
    pub licenses: HashMap<String, LicenseEntry>,
    #[serde(default)]
    // Key: e.g., "data:rules.yml"
    pub dataFiles: HashMap<String, DataFileEntry>,
    // Allow alias for backward compatibility
    #[serde(default, alias = "user_placeholders_cache")]
    // Key: standardized placeholder key (e.g., "fullname")
    pub userPlaceholders: HashMap<String, String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct LicenseEntry {
    pub spdxId: String,
    pub title: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nickname: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub filename: String,
    pub sha: String,
    // Raw tags
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub permissions: Vec<String>,
    // Raw tags
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub conditions: Vec<String>,
    // Raw tags
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub limitations: Vec<String>,
    // Full raw license body
    pub fileContentCached: String,
    // e.g., ["[fullname]", "[year]"]
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub placeholdersInBody: Vec<String>,
    pub infoComponents: InfoComponents,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct InfoComponents {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub howToApplyText: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub noteText: Option<String>,
    // Project name -> URL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usingInfo: Option<HashMap<String, String>>,
    #[serde(default)]
    pub parsedRules: ParsedRules,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct ParsedRules {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub permissions: Vec<RuleDetail>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub conditions: Vec<RuleDetail>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub limitations: Vec<RuleDetail>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RuleDetail {
    pub tag: String,
    pub label: String,
    pub description: String,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct DataFileEntry {
    pub sha: String,
    // Store parsed YAML content directly
    pub content: serde_yaml::Value,
}

#[derive(Deserialize, Debug, Clone)]
pub struct GitHubFile {
    pub name: String,
    // "file" or "dir"
    #[serde(rename = "type")]
    pub fileType: String,
    pub sha: String,
    // Present for files
    pub downloadUrl: Option<String>,
}

#[derive(Deserialize, Debug, Clone, Default)]
// To match YAML keys like "spdx-id"
#[serde(rename_all = "kebab-case")]
pub struct FrontMatter {
    // Optional because we might guess it
    pub spdxId: Option<String>,
    pub title: Option<String>,
    pub nickname: Option<String>,
    pub description: Option<String>,
    // "how to apply"
    pub how: Option<String>,
    pub note: Option<String>,
    #[serde(default)]
    pub permissions: Vec<String>,
    #[serde(default)]
    pub conditions: Vec<String>,
    #[serde(default)]
    pub limitations: Vec<String>,
    // Project name -> URL
    pub using: Option<HashMap<String, String>>,
}

// Example for rules.yml content
#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct RulesDataContent {
    #[serde(default)]
    pub permissions: Vec<RuleSource>,
    #[serde(default)]
    pub conditions: Vec<RuleSource>,
    #[serde(default)]
    pub limitations: Vec<RuleSource>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct RuleSource {
    pub tag: String,
    pub label: String,
    pub description: String,
}

// Example for fields.yml content
#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct FieldsDataContent {
    // Assuming the root is a list or a dict with a "fields" key
    #[serde(default, alias="fields")]
    pub items: Vec<FieldSource>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct FieldSource {
    // This is the placeholder name e.g., "fullname"
    pub name: String,
    pub description: String,
}