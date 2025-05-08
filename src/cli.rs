use clap::{Parser, Subcommand, Args as ClapArgs};
use std::path::PathBuf;
use clap::builder::TypedValueParser;

pub use clap_complete::Shell;

use crate::constants::CACHABLE_PLACEHOLDER_KEYS_ARRAY;


#[derive(Parser, Debug)]
#[clap(name = "getlicense", version = "0.1.0-rust", author = "Kaden Gruizenga")]
#[clap(about = "Fetches, displays, and manages open source license templates.", long_about = None)]
#[clap(propagate_version = true)]
pub struct Cli {
    #[clap(subcommand)]
    pub command: Option<Commands>,

    /// Force refresh of the local license and data cache from GitHub.
    #[clap(long, global = true)]
    pub refresh: bool,

    /// Path to the license cache file.
    #[clap(long, global = true, value_name = "FILE_PATH")]
    pub cacheFile: Option<PathBuf>,

    /// Print detailed status messages during execution (to stderr).
    #[clap(short, long, global = true)]
    pub verbose: bool,

    /// Generate shell completion script.
    #[clap(long = "generate-completion", value_enum, global = true, help = "Generate shell completion script for the specified shell")]
    pub generateCompletion: Option<Shell>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// List available licenses. If IDs provided, lists only those. Otherwise, lists all.
    List(ListArgs),
    /// List licenses with key details. If IDs provided, details only those. Otherwise, details all.
    #[clap(name = "detailed-list")]
    DetailedList(ListArgs),
    /// Show detailed metadata for a specific license.
    Info(InfoArgs),
    /// Show placeholders for a specific license.
    #[clap(name = "show-placeholders")]
    ShowPlaceholders(InfoArgs),
    /// Compare specified licenses. If no IDs, compares all available licenses.
    Compare(CompareArgs),
    /// Find licenses matching specified criteria.
    Find(FindArgs),
    /// Fill a license template with user-provided values and save it.
    License(LicenseFillArgs),
    /// Save a placeholder value for future use.
    #[clap(name = "set-placeholder", about = "Save a placeholder value. KEY must be one of: fullname, project, email, projecturl.")]
    SetPlaceholder(SetPlaceholderArgs),
    /// Show saved placeholder value(s). Shows all if no KEY.
    #[clap(name = "get-placeholder")]
    GetPlaceholder(GetPlaceholderArgs),
    /// Clear saved placeholder(s). Clears all if no KEY.
    #[clap(name = "clear-placeholders")]
    ClearPlaceholders(ClearPlaceholdersArgs),
}

#[derive(ClapArgs, Debug)]
pub struct ListArgs {
    /// SPDX IDs of the licenses to list/detail. Lists all if omitted.
    pub licenseIds: Option<Vec<String>>,
}

#[derive(ClapArgs, Debug)]
pub struct InfoArgs {
    /// SPDX ID of the license.
    pub licenseId: String,
}

#[derive(ClapArgs, Debug)]
pub struct CompareArgs {
    /// SPDX IDs of the licenses to compare. Compares all if omitted.
    pub licenseIds: Option<Vec<String>>,
}

#[derive(ClapArgs, Debug)]
pub struct FindArgs {
    /// List of rule tags that MUST be present.
    #[clap(long, value_name = "RULE_TAG", num_args = 1..)]
    pub require: Option<Vec<String>>,
    /// List of rule tags that MUST NOT be present.
    #[clap(long, value_name = "RULE_TAG", num_args = 1..)]
    pub disallow: Option<Vec<String>>,
}

#[derive(ClapArgs, Debug)]
pub struct LicenseFillArgs {
    /// SPDX ID of the license template to fill (case-insensitive).
    pub licenseId: String,
    /// Full name of the copyright holder.
    #[clap(short = 'f', long)]
    pub fullname: Option<String>,
    /// Copyright year. Defaults to current year (not saved in preferences).
    #[clap(short = 'y', long)]
    pub year: Option<String>,
    /// Project name.
    #[clap(short = 'p', long)]
    pub project: Option<String>,
    /// Email address.
    #[clap(short = 'e', long)]
    pub email: Option<String>,
    /// Project URL.
    #[clap(short = 'u', long)]
    pub projecturl: Option<String>,
    /// Output file path. Defaults to 'LICENSE'.
    #[clap(short = 'o', long, value_name = "OUTPUT_PATH")]
    pub output: Option<PathBuf>,
}

#[derive(ClapArgs, Debug)]
pub struct SetPlaceholderArgs {
    /// The placeholder key to set (e.g., "fullname", "project").
    #[clap(value_parser = clap::builder::PossibleValuesParser::new(CACHABLE_PLACEHOLDER_KEYS_ARRAY).map(|s| s.to_string()))]
    pub key: String,
    /// The value for the placeholder.
    pub value: String,
}

#[derive(ClapArgs, Debug)]
pub struct GetPlaceholderArgs {
    /// The placeholder key to retrieve. Shows all if omitted.
    #[clap(value_parser = clap::builder::PossibleValuesParser::new(CACHABLE_PLACEHOLDER_KEYS_ARRAY).map(|s| s.to_string()))]
    pub key: Option<String>,
}

#[derive(ClapArgs, Debug)]
pub struct ClearPlaceholdersArgs {
    /// Specific placeholder keys to clear. Clears all if omitted.
    #[clap(value_parser = clap::builder::PossibleValuesParser::new(CACHABLE_PLACEHOLDER_KEYS_ARRAY).map(|s| s.to_string()))]
    pub keys: Option<Vec<String>>,
}
