use crate::display;
use crate::error::AppError;
use crate::models::{Cache, LicenseEntry, RulesDataContent};
use std::sync::atomic::Ordering;

pub async fn CompareLicenses(
    cache: &Cache,
    requestedIds: Option<Vec<String>>,
) -> Result<(), AppError> {
    if crate::VERBOSE.load(Ordering::SeqCst) {
        eprintln!(
            "[Action] Comparing licenses. Requested IDs: {:?}",
            requestedIds
        );
    }

    let targetKeysLower: Vec<String> = match requestedIds {
        Some(ids) if !ids.is_empty() => ids
            .into_iter()
            .filter_map(|idStr| {
                // idStr is correct
                let idLower = idStr.to_lowercase();

                if cache.licenses.contains_key(&idLower) {
                    Some(idLower)
                } else {
                    eprintln!(
                        "[Action] Warning: License '{}' for comparison not found. Skipping.",
                        idStr
                    );
                    None
                }
            })
            .collect(),
        _ => {
            let mut allKeys: Vec<String> = cache.licenses.keys().cloned().collect();
            allKeys.sort();
            allKeys
        }
    };

    if targetKeysLower.len() < 2 {
        println!("Need at least two licenses to compare. Found {} valid licenses from request (or in cache if all).", targetKeysLower.len());

        return Ok(());
    }

    let mut licensesToCompare: Vec<&LicenseEntry> = Vec::new();

    for key in &targetKeysLower {
        if let Some(entry) = cache.licenses.get(key) {
            licensesToCompare.push(entry);
        }
    }

    if licensesToCompare.len() < 2 {
        println!(
            "After filtering, only {} licenses are available for comparison. Need at least two.",
            licensesToCompare.len()
        );

        return Ok(());
    }

    let rulesDataContent: Option<RulesDataContent> = cache
        .dataFiles // dataFiles is correct
        .get(crate::constants::RULES_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

    display::PrintComparisonTable(&licensesToCompare, &rulesDataContent);

    Ok(())
}
