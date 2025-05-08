use crate::models::Cache;
use crate::error::{AppError, ActionError};
use crate::constants::CACHABLE_PLACEHOLDER_KEYS;

pub async fn SetPlaceholder(
    cache: &mut Cache,
    key: &str,
    value: &str,
) -> Result<(), AppError> {

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Setting placeholder: {} = {}", key, value);
    }

    cache.user_placeholders.insert(key.to_string(), value.to_string());
    println!("Placeholder '{}' set to '{}' in saved preferences.",
        console::style(key).green(),
        console::style(value).cyan()
    );

    Ok(())
}

pub async fn GetPlaceholder(
    cache: &Cache,
    keyOpt: Option<&str>,
) -> Result<(), AppError> {

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Getting placeholder(s). Key: {:?}", keyOpt);
    }


    if cache.user_placeholders.is_empty() {
        println!("No saved placeholder preferences found.");

        return Ok(());
    }


    match keyOpt {
        Some(key) => {

            if let Some(value) = cache.user_placeholders.get(key) {
                println!("{}: {}", console::style(key).green(), console::style(value).cyan());
            } else {
                println!("No saved preference found for key '{}'.", console::style(key).yellow());
                let availableKeys: Vec<String> = cache.user_placeholders.keys().cloned().collect();

                if !availableKeys.is_empty() {
                    println!("Available saved keys: {}", availableKeys.join(", "));
                }
            }
        }
        None => {
            println!("{}", console::style("Saved Placeholder Preferences:").bold());
            let mut sortedPlaceholders: Vec<_> = cache.user_placeholders.iter().collect();
            sortedPlaceholders.sort_by_key(|(k,_)| *k);

            for (k, v) in sortedPlaceholders {
                println!("  {}: {}", console::style(k).green(), console::style(v).cyan());
            }
        }
    }

    Ok(())
}

pub async fn ClearPlaceholders(
    cache: &mut Cache,
    keysOpt: Option<Vec<String>>,
) -> Result<(), AppError> {

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Clearing placeholder(s). Keys: {:?}", keysOpt);
    }


    match keysOpt {
        Some(keysToClear) if !keysToClear.is_empty() => {
            let mut clearedAny = false;

            for key in keysToClear {

                if cache.user_placeholders.remove(&key).is_some() {
                    println!("Cleared saved preference for '{}'.", console::style(key).green());
                    clearedAny = true;
                } else {
                    println!("No saved preference found for key '{}' to clear.", console::style(key).yellow());
                }
            }

        }
        _ => {

            if cache.user_placeholders.is_empty() {
                println!("No saved placeholder preferences to clear.");
            } else {
                cache.user_placeholders.clear();
                println!("All saved placeholder preferences cleared.");
            }
        }
    }

    Ok(())
}