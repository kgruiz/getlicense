#![allow(non_snake_case)]

use clap::Parser;
use once_cell::sync::Lazy;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio;

mod cli;
// For Cache, etc. if used directly in main
mod actions;
mod cache;
mod constants;
mod error;
mod models;
// For potential direct calls or if actions re-export display functions
mod api;
mod display;
mod parser;

use cli::{Cli, Commands};
use constants::DEFAULT_CACHE_FILENAME;
use error::AppError;

// Global flag to indicate if cache was modified by an action (e.g. placeholder management)
// This helps decide if SaveCache needs to be called.
pub static VERBOSE: Lazy<AtomicBool> = Lazy::new(|| AtomicBool::new(false));
static CACHE_MODIFIED_BY_ACTION: Lazy<AtomicBool> = Lazy::new(|| AtomicBool::new(false));

#[tokio::main]
async fn main() -> Result<(), AppError> {
    let cli_args = Cli::parse();

    VERBOSE.store(cli_args.verbose, Ordering::SeqCst);

    if VERBOSE.load(Ordering::SeqCst) {
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

    if VERBOSE.load(Ordering::SeqCst) {
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
        Some(Commands::License(ref args)) => {
            // The fill action might modify the cache (user_placeholders)
            let modified_placeholder_cache =
                actions::fill::FillLicenseTemplateAction(&mut cache_data, args, &cli_args).await?;

            if modified_placeholder_cache {
                CACHE_MODIFIED_BY_ACTION.store(true, Ordering::SeqCst);
            }
        }
        Some(Commands::SetPlaceholder(args)) => {
            actions::placeholder_management::SetPlaceholder(
                &mut cache_data,
                &args.key,
                &args.value,
            )
            .await?;
            CACHE_MODIFIED_BY_ACTION.store(true, Ordering::SeqCst);
        }
        Some(Commands::GetPlaceholder(args)) => {
            actions::placeholder_management::GetPlaceholder(&cache_data, args.key.as_deref())
                .await?;
        }
        Some(Commands::ClearPlaceholders(args)) => {
            actions::placeholder_management::ClearPlaceholders(&mut cache_data, args.keys).await?;
            CACHE_MODIFIED_BY_ACTION.store(true, Ordering::SeqCst);
        }
        None => {
            action_was_handled = false;
        }
    }

    if !action_was_handled && cli_args.generateCompletion.is_none() {
        <Cli as clap::CommandFactory>::command()
            .print_help()
            .map_err(|e| AppError::Io(e, PathBuf::from("clap help")))?;
        // Instead of exiting with an error code, print a newline after the help
        // text and exit successfully.
        println!();
        return Ok(());
    }

    if cache_updated_by_fetch || CACHE_MODIFIED_BY_ACTION.load(Ordering::SeqCst) {
        if VERBOSE.load(Ordering::SeqCst) {
            eprintln!("Saving cache changes to {:?}...", cache_file_path);
        }

        cache::SaveCache(&cache_file_path, &cache_data)?;
    } else {
        if VERBOSE.load(Ordering::SeqCst) {
            eprintln!("No changes to save to cache file.");
        }
    }

    return Ok(());
}
