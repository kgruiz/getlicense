// --- GitHub API ---
pub const GITHUB_API_BASE_URL: &str = "https://api.github.com";
pub const GITHUB_API_VERSION_HEADER: &str = "application/vnd.github.v3+json";
pub const APP_USER_AGENT: &str = concat!(env!("CARGO_PKG_NAME"), "/", env!("CARGO_PKG_VERSION"));

pub const OWNER_CONST: &str = "github";
pub const REPO_CONST: &str = "choosealicense.com";
pub const BRANCH_CONST: &str = "gh-pages";

pub const LICENSES_PATH_STR: &str = "_licenses";
pub const DATA_PATH_STR: &str = "_data";

// --- Cache ---
pub const DEFAULT_CACHE_FILENAME: &str = "license_cache_rs.json";

// Specific data file keys (used to access them in the cache.data_files HashMap)
pub const RULES_YML_KEY: &str = "data:rules.yml";
pub const FIELDS_YML_KEY: &str = "data:fields.yml";

// --- Placeholder Management ---
// Standardized keys used internally for the user_placeholders cache and for CLI arg mapping.
// 'year' is intentionally excluded as it's not cached with user preferences.
pub const CACHABLE_PLACEHOLDER_KEYS: [&str; 4] = [
    "fullname",
    "project",
    "email",
    "projecturl",
];
pub const CACHABLE_PLACEHOLDER_KEYS_ARRAY: [&str; 4] = CACHABLE_PLACEHOLDER_KEYS;


// --- Mappings ---

// Maps CLI argument names to standardized cache keys
pub const CLI_ARG_TO_CACHE_KEY_TUPLES: [(&str, &str); 4] = [
    // clap arg dest name -> standard cache key
    ("fullname", "fullname"),
    ("project", "project"),
    ("email", "email"),
    ("projecturl", "projecturl"),
];

// Maps raw placeholder strings (found in license templates, keys are lowercased for matching)
// to standardized internal keys.
pub const RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES: [(&str, &str); 8] = [
    ("fullname", "fullname"),
    ("name of copyright owner", "fullname"),
    ("login", "fullname"),
    ("project", "project"),
    ("email", "email"),
    ("projecturl", "projecturl"),
    ("year", "year"),
    ("yyyy", "year"),
];

// Map standard placeholder keys to command-line argument suggestions
pub const PLACEHOLDER_TO_ARG_MAP_TUPLES: [(&str, &str); 9] = [
    ("fullname", "--fullname"),
    ("login", "--fullname (recommended for user/org name)"),
    ("email", "--email"),
    ("project", "--project"),
    ("description", "(no direct argument for '[description]')"),
    ("year", "--year"),
    ("projecturl", "--projecturl"),
    ("yyyy", "--year"),
    ("name of copyright owner", "--fullname"),
];


// --- Key Rules for Comparison Table ---
// (Label, tag_key_or_special_indicator)
pub const KEY_RULES_FOR_COMPARISON_ARRAY: [(&str, &str); 10] = [
    ("Commercial use", "commercial-use"),
    ("State changes", "document-changes"),
    ("Disclose source", "disclose-source"),
    ("Same license", "same-license"),
    ("License & copyright notice", "include-copyright"),
    ("Liability", "liability"),
    ("Warranty", "warranty"),
    ("Trademark use", "trademark-use"),
    // Special key for permissions
    ("Patent use (Perm)", "patent-use_perm"),
    // Special key for limitations
    ("Patent use (Lim)", "patent-use_lim"),
];
