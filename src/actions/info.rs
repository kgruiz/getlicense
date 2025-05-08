use crate::models::{Cache, FieldsDataContent, LicenseEntry};
use crate::display;
use crate::error::{AppError, ActionError};

pub async fn DisplayLicenseInfo(
    cache: &Cache,
    spdxIdStr: &str,
) -> Result<(), AppError> {
    let spdxIdLower = spdxIdStr.to_lowercase();

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Displaying info for license: {}", spdxIdLower);
    }

    match cache.licenses.get(&spdxIdLower) {
        Some(licenseEntry) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.data_files
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::print_license_info_panel(&licenseEntry, &fieldsDataContent);

            Ok(())
        }
        None =>

            Err(AppError::ActionError(ActionError::LicenseNotFound(spdxIdLower)))
    }
}

pub async fn ShowPlaceholdersForLicense(
    cache: &Cache,
    spdxIdStr: &str,
) -> Result<(), AppError> {
    let spdxIdLower = spdxIdStr.to_lowercase();

    if unsafe { crate::main::VERBOSE } {
        eprintln!("[Action] Showing placeholders for license: {}", spdxIdLower);
    }

    match cache.licenses.get(&spdxIdLower) {
        Some(licenseEntry) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.data_files
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::print_placeholder_list(
                &licenseEntry,
                &fieldsDataContent,
            );

            Ok(())
        }
        None =>

            Err(AppError::ActionError(ActionError::LicenseNotFound(spdxIdLower)))
    }
}