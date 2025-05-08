use std::path::Path;
use textwrap::{wrap, Options as TextWrapOptions};
use std::collections::HashMap;
use colored::*;

use crate::models::{Cache, LicenseEntry, RulesDataContent, FieldsDataContent};
use crate::cli::Cli as FullCliArgs;
use crate::constants::{
    KEY_RULES_FOR_COMPARISON_ARRAY, PLACEHOLDER_TO_ARG_MAP_TUPLES,
    RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES, CLI_ARG_TO_CACHE_KEY_TUPLES
};

fn PrintWrappedText(text: &str, indent: usize, width: usize) {
    let indentStr = " ".repeat(indent);
    let options = TextWrapOptions::new(width - indent).subsequent_indent(&indentStr);

    for line in wrap(text, options) {

        println!("{}{}", indentStr, line);

    }

}

pub fn PrintSimpleLicenseList(cache: &Cache, targetKeys: &[String]) {
    println!("\n{}", "Available Licenses (SPDX ID: Title):".bold());
    println!("{}", "-".repeat(50).dimmed());

    for key in targetKeys {

        if let Some(license) = cache.licenses.get(key) {

            println!("  {:<25} : {}",
                license.spdxId.cyan(), // spdxId is correct
                license.title
            );

        }


    }

}

pub fn PrintDetailedLicenseList(
    cache: &Cache,
    targetKeys: &[String],
    _rulesDataContent: &Option<RulesDataContent>,
) {

    for (i, key) in targetKeys.iter().enumerate() {

        if let Some(license) = cache.licenses.get(key) {

            println!("\n{}", format!("SPDX ID: {}", license.spdxId).cyan().bold()); // spdxId is correct
            println!("{}", format!("Title: {}", license.title).bold());


            if let Some(nick) = &license.nickname {

                println!("{}", format!("Nickname: {}", nick).italic()); // nickname is correct

            }


            if let Some(desc) = &license.description {

                 let shortDesc = textwrap::shorten(desc, 100, "...");
                 println!("{}: {}", "Description".bold(), shortDesc);

            }

            let parsedRules = &license.infoComponents.parsedRules; // infoComponents, parsedRules are correct

            for (catName, colorFn, rulesList) in [
                ("Permissions", ColoredString::green as fn(ColoredString)->ColoredString, &parsedRules.permissions),
                ("Conditions", ColoredString::yellow as fn(ColoredString)->ColoredString, &parsedRules.conditions),
                ("Limitations", ColoredString::red as fn(ColoredString)->ColoredString, &parsedRules.limitations),
            ] {

                let labels: Vec<&str> = rulesList.iter().map(|r| r.label.as_str()).collect();
                println!("{} ({}): {}",
                    colorFn(catName.bold()),
                    labels.len().to_string().blue(),
                    if labels.is_empty() { "None".dimmed().to_string() } else { labels.join(", ") }
                );

            }


            if i < targetKeys.len() - 1 {

                println!("{}", "---".dimmed());

            }


        }


    }

}

pub fn PrintLicenseInfoPanel(
    licenseEntry: &LicenseEntry,
    fieldsDataContent: &Option<FieldsDataContent>,
) {
    println!("\n--- {} ({}) ---",
        licenseEntry.title.bold(),
        licenseEntry.spdxId.bold() // spdxId is correct
    );


    if let Some(nick) = &licenseEntry.nickname {

        println!("\n{}", format!("Nickname: {}", nick).italic()); // nickname is correct

    }

    fn PrintTextBlockDisplay(label: &str, textOpt: Option<&String>) {

        if let Some(text) = textOpt {


            if !text.trim().is_empty() {

                println!("\n{}:", label.bold());
                PrintWrappedText(text, 2, 80);

            }


        }

    }

    PrintTextBlockDisplay("Description", licenseEntry.description.as_ref()); // description is correct
    PrintTextBlockDisplay("How to Apply", licenseEntry.infoComponents.howToApplyText.as_ref()); // infoComponents, howToApplyText are correct

    let parsedRules = &licenseEntry.infoComponents.parsedRules; // infoComponents, parsedRules are correct

    for (catName, colorFn, rulesList) in [
        ("Permissions", ColoredString::green as fn(ColoredString)->ColoredString, &parsedRules.permissions),
        ("Conditions", ColoredString::yellow as fn(ColoredString)->ColoredString, &parsedRules.conditions),
        ("Limitations", ColoredString::red as fn(ColoredString)->ColoredString, &parsedRules.limitations),
    ] {


        if !rulesList.is_empty() {

            println!("\n{}:", colorFn(catName.bold()));

            for ruleDetail in rulesList {

                println!("  - {} ({})",
                    colorFn(ruleDetail.label.bold()),
                    ruleDetail.tag.dimmed()
                );
                let shortDesc = textwrap::shorten(&ruleDetail.description, 80, "...");
                println!("    {}", shortDesc.italic().dimmed());

            }


        }


    }


    if let Some(usingMap) = &licenseEntry.infoComponents.usingInfo { // infoComponents, usingInfo are correct


        if !usingMap.is_empty() {

            println!("\n{}", "Notable Projects Using This License:".bold());

            for (project, url) in usingMap {

                println!("  - {}: {}", project, url);

            }


        }


    }

    PrintTextBlockDisplay("Note", licenseEntry.infoComponents.noteText.as_ref()); // infoComponents, noteText are correct

    let placeholderMapCliArgs: HashMap<_,_> = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect();


    if !licenseEntry.placeholdersInBody.is_empty() { // placeholdersInBody is correct

        println!("\n{}", "Placeholders in Body:".bold());

        for phFullStr in &licenseEntry.placeholdersInBody {

            let phNoBrackets = phFullStr.trim_matches(|c| c == '[' || c == ']');
            let phLower = phNoBrackets.to_lowercase();
            let mut description = "No description available".to_string();


            if let Some(fieldsContent) = fieldsDataContent {


                if let Some(fieldSrc) = fieldsContent.items.iter().find(|f| f.name.to_lowercase() == phLower) {

                    description = fieldSrc.description.clone();

                }


            }

            let argSuggestion = placeholderMapCliArgs.get(phLower.as_str()).unwrap_or(&"(no direct argument)");
            let defaultInfo = if phLower == "year" || phLower == "yyyy" { " (defaults to current year)" } else { "" };
            println!("  - {}", phFullStr.magenta().bold());
            println!("    {}: {}", "Description".dimmed(), description);
            println!("    {}: {}{}", "Argument".dimmed(), argSuggestion, defaultInfo);

        }


    } else {

        println!("\n{}: {}", "Placeholders in Body".bold(), "(None detected)".dimmed());

    }

}

pub fn PrintPlaceholderList(
    licenseEntry: &LicenseEntry,
    fieldsDataContent: &Option<FieldsDataContent>,
) {
    println!("\n--- {} ({}) ---",
        format!("Placeholders for {}", licenseEntry.title).bold(),
        licenseEntry.spdxId.bold() // spdxId is correct
    );
    let placeholderMapCliArgs: HashMap<_,_> = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect();


    if licenseEntry.placeholdersInBody.is_empty() { // placeholdersInBody is correct

        println!("  {}", "(No standard [placeholder] patterns found)".dimmed());

    } else {


        for phFullStr in &licenseEntry.placeholdersInBody { // placeholdersInBody is correct

            let phNoBrackets = phFullStr.trim_matches(|c| c == '[' || c == ']');
            let phLower = phNoBrackets.to_lowercase();
            let mut description = "No description available".to_string();


            if let Some(fieldsContent) = fieldsDataContent {


                if let Some(fieldSrc) = fieldsContent.items.iter().find(|f| f.name.to_lowercase() == phLower) {

                    description = fieldSrc.description.clone();

                }


            }

            let argSuggestion = placeholderMapCliArgs.get(phLower.as_str()).unwrap_or(&"(no direct argument)");
            let defaultInfo = if phLower == "year" || phLower == "yyyy" { " (defaults to current year if not provided)" } else { "" };
            println!("  - {}", phFullStr.magenta().bold());
            println!("    {}: {}", "Description".dimmed(), description);
            println!("    {}: {}{}", "Argument".dimmed(), argSuggestion, defaultInfo);

        }


    }

}

pub fn PrintComparisonTable(
    licensesToCompare: &[&LicenseEntry],
    _rulesDataContent: &Option<RulesDataContent>,
) {
    let licenseNames: Vec<String> = licensesToCompare.iter().map(|l| l.spdxId.clone()).collect(); // spdxId is correct
    println!("Comparing: {}", licenseNames.join(", ").cyan());
    println!("\n{}", "Key Rule Indicators Table (Simplified):".bold());

    print!("{:<20}", "SPDX ID".cyan());

    for (label, _) in KEY_RULES_FOR_COMPARISON_ARRAY.iter() {

        let wrappedLabelParts: Vec<String> = textwrap::wrap(label, 10).iter().map(|s| s.to_string()).collect();
        print!(" {:<12}", wrappedLabelParts.get(0).unwrap_or(&"".to_string()));

    }

    println!();
    print!("{:<20}", "");

    for (label, _) in KEY_RULES_FOR_COMPARISON_ARRAY.iter() {

        let wrappedLabelParts: Vec<String> = textwrap::wrap(label, 10).iter().map(|s| s.to_string()).collect();
        print!(" {:<12}", wrappedLabelParts.get(1).unwrap_or(&"".to_string()));

    }

    println!();


    for license in licensesToCompare {

        print!("{:<20}", license.spdxId.cyan()); // spdxId is correct

        for (_, tagKey) in KEY_RULES_FOR_COMPARISON_ARRAY.iter() {

            let mut hasRule = false;


            if tagKey.ends_with("_perm") {

                let baseTag = tagKey.trim_end_matches("_perm");

                if license.permissions.contains(&baseTag.to_string()) { hasRule = true; }

            } else if tagKey.ends_with("_lim") {

                let baseTag = tagKey.trim_end_matches("_lim");

                if license.limitations.contains(&baseTag.to_string()) { hasRule = true; }

            } else if license.permissions.contains(&tagKey.to_string()) ||
                      license.conditions.contains(&tagKey.to_string()) ||
                      license.limitations.contains(&tagKey.to_string()) {

                hasRule = true;

            }

            let indicator = if hasRule { "  ✓  ".green().bold() } else { "  X  ".red().bold() };
            print!(" {:<12}", indicator);

        }

        println!();

    }

}

pub fn PrintFindResults(matches: &[&LicenseEntry], requireTags: &[String], disallowTags: &[String]) {
    println!("Require: {}", if requireTags.is_empty() { "None".dimmed().to_string() } else { requireTags.join(", ").green().to_string() });
    println!("Disallow: {}", if disallowTags.is_empty() { "None".dimmed().to_string() } else { disallowTags.join(", ").red().to_string() });
    println!("{}", "-".repeat(50).dimmed());


    if matches.is_empty() {

        println!("No licenses found matching all criteria.");

    } else {

        println!("Found {} matching license(s):", matches.len());

        for license in matches {

            println!("  - {} ({})", license.spdxId.cyan(), license.title); // spdxId is correct

        }


    }

}

pub fn DisplayLicenseSummaryAfterWrite(
    licenseEntry: &LicenseEntry,
    cache: &Cache,
    outputPath: &Path,
    userProvidedForFilling: &std::collections::HashMap<String, String>,
    cachedPlaceholdersAtStart: &std::collections::HashMap<String, String>,
    filledLicenseBody: &str,
    cliAllArgs: &FullCliArgs,
) {
    println!("\n--- {} written to {} ---",
        licenseEntry.title.bold(),
        outputPath.display().to_string().green() // outputPath is correct
    );


    if let Some(nick) = &licenseEntry.nickname {

        println!("\n{}", format!("Nickname: {}", nick).italic()); // nickname is correct

    }

    fn PrintTextBlockSummary(label: &str, textOpt: Option<&String>) {

        if let Some(text) = textOpt {


            if !text.trim().is_empty() {

                println!("\n{}:", label.bold());
                PrintWrappedText(text, 2, 80);

            }


        }

    }

    PrintTextBlockSummary("Description", licenseEntry.description.as_ref()); // description is correct

    let parsedRules = &licenseEntry.infoComponents.parsedRules; // infoComponents, parsedRules are correct

    for (catName, colorFn, rulesList) in [
        ("Permissions", ColoredString::green as fn(ColoredString)->ColoredString, &parsedRules.permissions),
        ("Conditions", ColoredString::yellow as fn(ColoredString)->ColoredString, &parsedRules.conditions),
        ("Limitations", ColoredString::red as fn(ColoredString)->ColoredString, &parsedRules.limitations),
    ] {


        if !rulesList.is_empty() {

            println!("\n{}:", colorFn(catName.bold()));

            for ruleDetail in rulesList {

                println!("  - {} ({})",
                    colorFn(ruleDetail.label.bold()),
                    ruleDetail.tag.dimmed()
                );

            }


        }


    }

    PrintTextBlockSummary("Note", licenseEntry.infoComponents.noteText.as_ref()); // infoComponents, noteText are correct

    let placeholderMapCliArgs: HashMap<_,_> = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect();
    let rawPhToStdKeyMap: HashMap<_,_> = RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES.iter().cloned().collect();
    let cliArgToCacheKeyMap: HashMap<_,_> = CLI_ARG_TO_CACHE_KEY_TUPLES.iter().cloned().collect();

    let fieldsDataContent: Option<FieldsDataContent> = cache.dataFiles // dataFiles is correct
        .get(crate::constants::FIELDS_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());


    if !licenseEntry.placeholdersInBody.is_empty() {

        println!("\n{}", "Placeholder Values Used:".bold());

        for phFullStr in &licenseEntry.placeholdersInBody { // placeholdersInBody is correct

            let phNoBrackets = phFullStr.trim_matches(|c| c == '[' || c == ']');
            let phLower = phNoBrackets.to_lowercase();
            let standardKeyOpt = rawPhToStdKeyMap.get(phLower.as_str());

            let mut sourceInfo = "".to_string();
            let mut valueUsedStr = "".to_string();


            if let Some(standardKey) = standardKeyOpt {

                let valueActuallyUsed = userProvidedForFilling.get(*standardKey);

                if let Some(valUsed) = valueActuallyUsed {

                    valueUsedStr = format!(" (Value: \"{}\")", valUsed);

                }


                if *standardKey == "year" {

                    let yearArgExplicitlyPassed = cliAllArgs.command.as_ref().map_or(false, |cmd| {

                        if let crate::cli::Commands::License(lic_args) = cmd { lic_args.year.is_some() } else { false }

                    });
                    sourceInfo = if yearArgExplicitlyPassed { "CLI argument (--year)".cyan().to_string() }
                                  else { "Defaulted (current year)".blue().to_string() };

                } else if userProvidedForFilling.contains_key(*standardKey) &&
                          cliArgToCacheKeyMap.values().any(|&vStdKey| vStdKey == *standardKey) &&
                          cliAllArgs.command.as_ref().map_or(false, |cmd| {

                              if let crate::cli::Commands::License(licArgs) = cmd {

                                  match *standardKey {
                                      "fullname" => licArgs.fullname.is_some(), "project" => licArgs.project.is_some(),
                                      "email" => licArgs.email.is_some(), "projecturl" => licArgs.projecturl.is_some(),
                                      _ => false,
                                  }

                              } else { false }

                          })
                {
                    let cliArgName = placeholderMapCliArgs.get(phLower.as_str()).unwrap_or(&"CLI arg");
                    sourceInfo = format!("CLI argument ({})", cliArgName).cyan().to_string();

                } else if cachedPlaceholdersAtStart.contains_key(*standardKey) {

                    sourceInfo = "Saved preference (cache)".yellow().to_string();

                } else {

                    sourceInfo = "Not specified".red().to_string();

                    if filledLicenseBody.contains(phFullStr) {

                        sourceInfo.push_str(&format!(" ({})", "remains in file!".red().bold()));

                    }

                    valueUsedStr = "".to_string();

                }


            } else {

                sourceInfo = "Unknown placeholder".magenta().to_string();

                if filledLicenseBody.contains(phFullStr) {

                     sourceInfo.push_str(&format!(" ({})", "remains in file!".red().bold()));

                }

                valueUsedStr = "".to_string();

            }

            println!("  - {}: {}{}", phFullStr.magenta().bold(), sourceInfo, valueUsedStr);

        }


    } else {

        println!("\n{}: {}", "Placeholder Values Used".bold(), "(No standard placeholders in template)".dimmed());

    }

}
