use clap::Parser;
use std::io;
use std::path::PathBuf;
use tokio;

mod cli;
// For Cache, etc. if used directly in main
mod models;
mod cache;
mod actions;
mod error;
mod constants;
// For potential direct calls or if actions re-export display functions
mod display;
mod parser;
mod api;

use cli::{Cli, Commands};
use error::AppError;
use constants::DEFAULT_CACHE_FILENAME;

// Global flag to indicate if cache was modified by an action (e.g. placeholder management)
// This helps decide if SaveCache needs to be called.
pub static mut VERBOSE: bool = false;
static mut CACHE_MODIFIED_BY_ACTION: bool = false;


#[tokio::main]
async fn main() -> Result<(), AppError> {
    let cli_args = Cli::parse();

    // SAFETY: Single-threaded access at program start.
    unsafe {
        VERBOSE = cli_args.verbose;
    }

    if unsafe { VERBOSE } {
        eprintln!("Verbose mode enabled.");
    }

    if let Some(shell) = cli_args.generateCompletion {
        let mut cmd = <Cli as clap::CommandFactory>::command();
        let app_name = cmd.get_name().to_string();
        clap_complete::generate(shell, &mut cmd, app_name, &mut io::stdout());

        return Ok(());

    }

    let cache_file_path = cli_args.cacheFile.clone().unwrap_or_else(|| {
        // Attempt to construct a default path, e.g., in user's config directory
        let home_dir = dirs::home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
        home_dir.join(DEFAULT_CACHE_FILENAME)
    });

    if unsafe { crate::VERBOSE } {
        eprintln!("Using cache file: {:?}", cache_file_path);
    }

    let (mut cache_data, cache_updated_by_fetch) =
        cache::UpdateAndLoadLicenseCache(&cache_file_path, cli_args.refresh).await?;

    let mut action_was_handled = true;

    match cli_args.command {
        Some(Commands::List(args)) => {
            actions::list::ListLicenses(&cache_data, args.licenseIds).await?;
        }
        Some(Commands::DetailedList(args)) => {
            actions::list::DetailedListLicenses(&cache_data, args.licenseIds).await?;
        }
        Some(Commands::Info(args)) => {
            actions::info::DisplayLicenseInfo(&cache_data, &args.licenseId).await?;
        }
        Some(Commands::ShowPlaceholders(args)) => {
            actions::info::ShowPlaceholdersForLicense(&cache_data, &args.licenseId).await?;
        }
        Some(Commands::Compare(args)) => {
            actions::compare::CompareLicenses(&cache_data, args.licenseIds).await?;
        }
        Some(Commands::Find(args)) => {
            actions::find::FindMatchingLicenses(&cache_data, args.require, args.disallow).await?;
        }
        Some(Commands::License(args)) => {
            // The fill action might modify the cache (user_placeholders)
            let modified_placeholder_cache = actions::fill::FillLicenseTemplateAction(
                &mut cache_data,
                &args,
                &cli_args,
            )
            .await?;

            if modified_placeholder_cache {
                // SAFETY: Single-threaded logical section.
                unsafe { CACHE_MODIFIED_BY_ACTION = true; }
            }

        }
        Some(Commands::SetPlaceholder(args)) => {
            actions::placeholder_management::SetPlaceholder(&mut cache_data, &args.key, &args.value).await?;
            // SAFETY: Single-threaded logical section.
            unsafe { CACHE_MODIFIED_BY_ACTION = true; }
        }
        Some(Commands::GetPlaceholder(args)) => {
            actions::placeholder_management::GetPlaceholder(&cache_data, args.key.as_deref()).await?;
        }
        Some(Commands::ClearPlaceholders(args)) => {
            actions::placeholder_management::ClearPlaceholders(&mut cache_data, args.keys).await?;
            // SAFETY: Single-threaded logical section.
            unsafe { CACHE_MODIFIED_BY_ACTION = true; }
        }
        None => {
            action_was_handled = false;
        }
    }

    if !action_was_handled && cli_args.generateCompletion.is_none() {
        <Cli as clap::CommandFactory>::command().print_help().map_err(|e| AppError::Io(e, PathBuf::from("clap help")))?;
        eprintln!("\nNo action specified. Use --help for usage information.");
        std::process::exit(1);
    }

    if cache_updated_by_fetch || unsafe { CACHE_MODIFIED_BY_ACTION } {

        if unsafe { crate::VERBOSE } {
            eprintln!("Saving cache changes to {:?}...", cache_file_path);
        }

        cache::SaveCache(&cache_file_path, &cache_data).await?;

    }

    else {
        if unsafe { crate::VERBOSE } {
            eprintln!("No changes to save to cache file.");
        }

    }

    return Ok(());
}
