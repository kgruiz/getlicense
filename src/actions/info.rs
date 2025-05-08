use crate::models::{Cache, FieldsDataContent, LicenseEntry};
use crate::display;
use crate::error::{AppError, ActionError};

pub async fn DisplayLicenseInfo(
    cache: &Cache,
    spdxIdStr: &str,
) -> Result<(), AppError> {
    let spdxIdLower = spdxIdStr.to_lowercase();

    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Displaying info for license: {}", spdxIdLower);
    }

    match cache.licenses.get(&spdxIdLower) {
        Some(licenseEntry) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.dataFiles // dataFiles is correct
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::PrintLicenseInfoPanel(&licenseEntry, &fieldsDataContent);

            Ok(())
        }
        None =>
            Err(AppError::ActionErrorVariant(ActionError::LicenseNotFound(spdxIdLower)))
    }
}

pub async fn ShowPlaceholdersForLicense(
    cache: &Cache,
    spdxIdStr: &str,
) -> Result<(), AppError> {
    let spdxIdLower = spdxIdStr.to_lowercase();

    if unsafe { crate::VERBOSE } {
        eprintln!("[Action] Showing placeholders for license: {}", spdxIdLower);
    }

    match cache.licenses.get(&spdxIdLower) {
        Some(licenseEntry) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.dataFiles // dataFiles is correct
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::PrintPlaceholderList(
                &licenseEntry,
                &fieldsDataContent,
            );

            Ok(())
        }
        None =>
            Err(AppError::ActionErrorVariant(ActionError::LicenseNotFound(spdxIdLower)))
    }
}
