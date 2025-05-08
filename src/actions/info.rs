use crate::models::{Cache, FieldsDataContent};
use crate::display;
use crate::error::{AppError, ActionError};
use crate::cache;

pub async fn DisplayLicenseInfo(
    cache: &Cache,
    spdxIdStr: &str,
) -> Result<(), AppError> {
    let spdxIdLower = spdxIdStr.to_lowercase();

    if unsafe { crate::main::VERBOSE } {

        eprintln!("[Action] Displaying info for license: {}", spdxIdLower);

    }


    match cache::GetFullLicenseDataFromCache(spdxIdLower.as_str(), cache) {

        Ok(Some(licenseEntry)) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.data_files
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::PrintLicenseInfoPanel(&licenseEntry, &fieldsDataContent);

            Ok(())
        }
        Ok(None) => {

            Err(AppError::ActionError(ActionError::LicenseNotFound(spdxIdLower)))

        }
        Err(e) => {

            Err(AppError::CacheError(e))

        }
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


    match cache::GetFullLicenseDataFromCache(spdxIdLower.as_str(), cache) {

        Ok(Some(licenseEntry)) => {
            let fieldsDataContent: Option<FieldsDataContent> = cache.data_files
                .get(crate::constants::FIELDS_YML_KEY)
                .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());

            display::PrintPlaceholderList(
                &licenseEntry,
                &fieldsDataContent,
            );

            Ok(())
        }
        Ok(None) => {

            Err(AppError::ActionError(ActionError::LicenseNotFound(spdxIdLower)))

        }
        Err(e) => {

            Err(AppError::CacheError(e))

        }
    }
}