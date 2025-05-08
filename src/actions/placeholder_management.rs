use colored::*;
use crate::models::Cache;
use crate::error::{AppError, ActionError};
use crate::constants::CACHABLE_PLACEHOLDER_KEYS;

pub async fn SetPlaceholder(
    cache: &mut Cache,
    key: &str,
    value: &str,
) -> Result<(), AppError> {

    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Setting placeholder: {} = {}", key, value);
    }

    cache.userPlaceholders.insert(key.to_string(), value.to_string());
    println!("Placeholder '{}' set to '{}' in saved preferences.",
        key.green(),
        value.cyan()
    );

    Ok(())
}

pub async fn GetPlaceholder(
    cache: &Cache,
    keyOpt: Option<&str>,
) -> Result<(), AppError> {

    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Getting placeholder(s). Key: {:?}", keyOpt);
    }


    if cache.userPlaceholders.is_empty() {
        println!("No saved placeholder preferences found.");

        return Ok(());
    }


    match keyOpt {
        Some(key) => {

            if let Some(value) = cache.userPlaceholders.get(key) {
                println!("{}: {}", key.green(), value.cyan());
            } else {
                println!("No saved preference found for key '{}'.", key.yellow());
                let availableKeys: Vec<String> = cache.userPlaceholders.keys().cloned().collect();

                if !availableKeys.is_empty() {
                    println!("Available saved keys: {}", availableKeys.join(", "));
                }
            }
        }
        None => {
            println!("{}", "Saved Placeholder Preferences:".bold());
            let mut sortedPlaceholders: Vec<_> = cache.userPlaceholders.iter().collect();
            sortedPlaceholders.sort_by_key(|(k,_)| *k);

            for (k, v) in sortedPlaceholders {
                println!("  {}: {}", k.green(), v.cyan());
            }
        }
    }

    Ok(())
}

pub async fn ClearPlaceholders(
    cache: &mut Cache,
    keysOpt: Option<Vec<String>>,
) -> Result<(), AppError> {

    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Clearing placeholder(s). Keys: {:?}", keysOpt);
    }


    match keysOpt {
        Some(keysToClear) if !keysToClear.is_empty() => {
            let mut clearedAny = false;

            for key in keysToClear {

                if cache.userPlaceholders.remove(&key).is_some() {
                    println!("Cleared saved preference for '{}'.", key.green());
                    clearedAny = true;
                } else {
                    println!("No saved preference found for key '{}' to clear.", key.yellow());
                }
            }

        }
        _ => {

            if cache.userPlaceholders.is_empty() {
                println!("No saved placeholder preferences to clear.");
            } else {
                cache.userPlaceholders.clear();
                println!("All saved placeholder preferences cleared.");
            }
        }
    }

    Ok(())
}
