use crate::models::Cache;
use crate::display;
use crate::error::AppError;

fn GetTargetLicenseKeys(
    cache: &Cache,
    requestedIds: Option<Vec<String>>,
) -> Vec<String> {

    match requestedIds {

        Some(ids) if !ids.is_empty() => ids
            .into_iter()
            .filter_map(|idStr| {
                let idLower = idStr.to_lowercase();

                if cache.licenses.contains_key(&idLower) {
                    Some(idLower)
                }

                else {
                    eprintln!("[Action] Warning: License '{}' not found in cache. Skipping.", idStr);
                    None
                }

            })
            .collect(),

        _ => {
            let mut allKeys: Vec<String> = cache.licenses.keys().cloned().collect();
            allKeys.sort();
            allKeys
        }

    }
}

pub async fn ListLicenses(
    cache: &Cache,
    requestedIds: Option<Vec<String>>,
) -> Result<(), AppError> {

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Listing licenses. Requested IDs: {:?}", requestedIds);
    }

    let targetKeys = GetTargetLicenseKeys(cache, requestedIds);

    if targetKeys.is_empty() {

        if cache.licenses.is_empty() {
            println!("No licenses found in the cache.");
        }

        else {
            println!("No matching licenses found for the specified IDs, or no IDs provided and cache is empty.");
        }

        return Ok(());

    }

    display::print_simple_license_list(cache, &targetKeys);

    return Ok(());

}

pub async fn DetailedListLicenses(
    cache: &Cache,
    requestedIds: Option<Vec<String>>,
) -> Result<(), AppError> {

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Detailed listing of licenses. Requested IDs: {:?}", requestedIds);
    }

    let targetKeys = GetTargetLicenseKeys(cache, requestedIds);

    if targetKeys.is_empty() {

         if cache.licenses.is_empty() {
            println!("No licenses found in the cache for detailed listing.");
        }

        else {
            println!("No matching licenses found for detailed listing with specified IDs, or no IDs provided and cache is empty.");
        }

        return Ok(());

    }

    // The display function will need access to rules.yml for labels
    let rulesDataContent = cache.data_files.get(crate::constants::RULES_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

    display::print_detailed_license_list(cache, &targetKeys, &rulesDataContent);

    return Ok(());

}