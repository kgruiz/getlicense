use std::path::Path;
use textwrap::{wrap, Options as TextWrapOptions};
use chrono::Datelike;

use crate::models::{Cache, LicenseEntry, RulesDataContent, FieldsDataContent, RuleDetail};
use crate::cli::Cli as FullCliArgs;
use crate::constants::{KEY_RULES_FOR_COMPARISON_ARRAY, PLACEHOLDER_TO_ARG_MAP_TUPLES, RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES, CLI_ARG_TO_CACHE_KEY_TUPLES};

// Basic console styling helper
mod console {
    pub fn style<S: AsRef<str>>(text: S) -> StyledText {
        StyledText { text: text.as_ref().to_string(), styles: Vec::new() }
    }
    pub struct StyledText { text: String, styles: Vec<&'static str> }
    impl StyledText {
        pub fn bold(mut self) -> Self { self.styles.push("bold"); self }
        pub fn cyan(mut self) -> Self { self.styles.push("cyan"); self }
        pub fn green(mut self) -> Self { self.styles.push("green"); self }
        pub fn yellow(mut self) -> Self { self.styles.push("yellow"); self }
        pub fn red(mut self) -> Self { self.styles.push("red"); self }
        pub fn magenta(mut self) -> Self { self.styles.push("magenta"); self }
        pub fn blue(mut self) -> Self { self.styles.push("blue"); self }
        pub fn italic(mut self) -> Self { self.styles.push("italic"); self }
        pub fn dim(mut self) -> Self { self.styles.push("dim"); self }
    }
    impl std::fmt::Display for StyledText {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            // In a real implementation, this would apply ANSI codes
            write!(f, "{}", self.text)
        }
    }
}


fn PrintWrappedText(text: &str, indent: usize, width: usize) {
    let indentStr = " ".repeat(indent);
    let options = TextWrapOptions::new(width - indent).subsequent_indent(&indentStr);

    for line in wrap(text, options) {
        println!("{}{}", indentStr, line);
    }

}

pub fn PrintSimpleLicenseList(cache: &Cache, targetKeys: &[String]) {
    println!("\n{}", console::style("Available Licenses (SPDX ID: Title):").bold());
    println!("{}", console::style("-".repeat(50)).dim());

    for key in targetKeys {

        if let Some(license) = cache.licenses.get(key) {
            println!("  {:<25} : {}",
                console::style(&license.spdx_id).cyan(),
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
            println!("\n{}", console::style(format!("SPDX ID: {}", license.spdx_id)).cyan().bold());
            println!("{}", console::style(format!("Title: {}", license.title)).bold());

            if let Some(nick) = &license.nickname {
                println!("{}", console::style(format!("Nickname: {}", nick)).italic());
            }

            if let Some(desc) = &license.description {
                 let shortDesc = textwrap::shorten(desc, 100, "...");
                 println!("{}: {}", console::style("Description").bold(), shortDesc);
            }

            let parsedRules = &license.info_components.parsed_rules;

            for (catName, color, rulesList) in [
                ("Permissions", "green", &parsedRules.permissions),
                ("Conditions", "yellow", &parsedRules.conditions),
                ("Limitations", "red", &parsedRules.limitations),
            ] {
                let labels: Vec<&str> = rulesList.iter().map(|r| r.label.as_str()).collect();
                println!("{} ([blue]{}[/blue]): {}",
                    console::style(catName).bold().paint(color),
                    labels.len(),
                    if labels.is_empty() { console::style("None").dim().to_string() } else { labels.join(", ") }
                );
            }

            if i < targetKeys.len() - 1 {
                println!("{}", console::style("---").dim());
            }

        }

    }
}

pub fn PrintLicenseInfoPanel(
    licenseEntry: &LicenseEntry,
    fieldsDataContent: &Option<FieldsDataContent>,
) {
    println!("\n--- {} ({}) ---",
        console::style(&licenseEntry.title).bold(),
        console::style(&licenseEntry.spdx_id).bold()
    );

    if let Some(nick) = &licenseEntry.nickname {
        println!("\n{}", console::style(format!("Nickname: {}", nick)).italic());
    }

    fn print_text_block_display(label: &str, textOpt: Option<&String>) {

        if let Some(text) = textOpt {

            if !text.trim().is_empty() {
                println!("\n{}:", console::style(label).bold());
                PrintWrappedText(text, 2, 80);
            }

        }

    }

    print_text_block_display("Description", licenseEntry.description.as_ref());
    print_text_block_display("How to Apply", licenseEntry.info_components.how_to_apply_text.as_ref());

    let parsedRules = &licenseEntry.info_components.parsed_rules;

    for (catName, color, rulesList) in [
        ("Permissions", "green", &parsedRules.permissions),
        ("Conditions", "yellow", &parsedRules.conditions),
        ("Limitations", "red", &parsedRules.limitations),
    ] {

        if !rulesList.is_empty() {
            println!("\n{}:", console::style(catName).bold().paint(color));

            for ruleDetail in rulesList {
                println!("  - {} ({})",
                    console::style(&ruleDetail.label).bold().paint(color),
                    console::style(&ruleDetail.tag).dim()
                );
                let shortDesc = textwrap::shorten(&ruleDetail.description, 80, "...");
                println!("    {}", console::style(shortDesc).italic().dim());
            }

        }

    }

    if let Some(usingMap) = &licenseEntry.info_components.using_info {

        if !usingMap.is_empty() {
            println!("\n{}", console::style("Notable Projects Using This License:").bold());

            for (project, url) in usingMap {
                println!("  - {}: {}", project, url);
            }

        }

    }

    print_text_block_display("Note", licenseEntry.info_components.note_text.as_ref());

    let placeholderMapCliArgs = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect::<std::collections::HashMap<_,_>>();

    if !licenseEntry.placeholders_in_body.is_empty() {
        println!("\n{}", console::style("Placeholders in Body:").bold());

        for phFullStr in &licenseEntry.placeholders_in_body {
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

            println!("  - {}", console::style(phFullStr).magenta().bold());
            println!("    {}: {}", console::style("Description").dim(), description);
            println!("    {}: {}{}", console::style("Argument").dim(), argSuggestion, defaultInfo);
        }

    }

    else {
        println!("\n{}: {}", console::style("Placeholders in Body").bold(), console::style("(None detected)").dim());
    }
}


pub fn PrintPlaceholderList(
    licenseEntry: &LicenseEntry,
    fieldsDataContent: &Option<FieldsDataContent>,
) {
     println!("\n--- {} ({}) ---",
        console::style(format!("Placeholders for {}", licenseEntry.title)).bold(),
        console::style(&licenseEntry.spdx_id).bold()
    );
    let placeholderMapCliArgs = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect::<std::collections::HashMap<_,_>>();

    if licenseEntry.placeholders_in_body.is_empty() {
        println!("  {}", console::style("(No standard [placeholder] patterns found)").dim());
    }

    else {

        for phFullStr in &licenseEntry.placeholders_in_body {
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

            println!("  - {}", console::style(phFullStr).magenta().bold());
            println!("    {}: {}", console::style("Description").dim(), description);
            println!("    {}: {}{}", console::style("Argument").dim(), argSuggestion, defaultInfo);
        }

    }
}


pub fn PrintComparisonTable(
    licensesToCompare: &[&LicenseEntry],
    _rulesDataContent: &Option<RulesDataContent>,
) {
    let licenseNames: Vec<String> = licensesToCompare.iter().map(|l| l.spdx_id.clone()).collect();
    println!("Comparing: {}", console::style(licenseNames.join(", ")).cyan());

    println!("\n{}", console::style("Key Rule Indicators Table (Simplified):").bold());

    let keyRules: std::collections::OrderedDict<String, String> = KEY_RULES_FOR_COMPARISON_ARRAY.iter().map(|(k,v)| (k.to_string(), v.to_string())).collect();

    print!("{:<20}", console::style("SPDX ID").cyan());

    for (label, _) in &keyRules {
        let wrappedLabelParts: Vec<String> = textwrap::wrap(label, 10).iter().map(|s| s.to_string()).collect();
        print!(" {:<12}", wrappedLabelParts.get(0).unwrap_or(&"".to_string()));
    }

    println!();
    print!("{:<20}", "");

     for (label, _) in &keyRules {
        let wrappedLabelParts: Vec<String> = textwrap::wrap(label, 10).iter().map(|s| s.to_string()).collect();
        print!(" {:<12}", wrappedLabelParts.get(1).unwrap_or(&"".to_string()));
    }

    println!();


    for license in licensesToCompare {
        print!("{:<20}", console::style(&license.spdx_id).cyan());

        for (_, tagKey) in &keyRules {
            let mut hasRule = false;
            if tagKey.ends_with("_perm") {
                let baseTag = tagKey.trim_end_matches("_perm");

                if license.permissions.contains(&baseTag.to_string()) { hasRule = true; }

            }
            else if tagKey.ends_with("_lim") {
                let baseTag = tagKey.trim_end_matches("_lim");

                if license.limitations.contains(&baseTag.to_string()) { hasRule = true; }

            }
            else if license.permissions.contains(tagKey) ||
                      license.conditions.contains(tagKey) ||
                      license.limitations.contains(tagKey) {
                hasRule = true;
            }

            let indicator = if hasRule { console::style("  ✓  ").green().bold() } else { console::style("  X  ").red().bold() };
            print!(" {:<12}", indicator);
        }

        println!();
    }
}

pub fn PrintFindResults(matches: &[&LicenseEntry], requireTags: &[String], disallowTags: &[String]) {
    println!("Require: {}", if requireTags.is_empty() { console::style("None").dim().to_string() } else { console::style(requireTags.join(", ")).green().to_string() });
    println!("Disallow: {}", if disallowTags.is_empty() { console::style("None").dim().to_string() } else { console::style(disallowTags.join(", ")).red().to_string() });
    println!("{}", console::style("-".repeat(50)).dim());

    if matches.is_empty() {
        println!("No licenses found matching all criteria.");
    }

    else {
        println!("Found {} matching license(s):", matches.len());

        for license in matches {
            println!("  - {} ({})", console::style(&license.spdx_id).cyan(), license.title);
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
        console::style(&licenseEntry.title).bold(),
        console::style(outputPath.display().to_string()).green()
    );

    if let Some(nick) = &licenseEntry.nickname {
        println!("\n{}", console::style(format!("Nickname: {}", nick)).italic());
    }

    fn print_text_block_summary(label: &str, textOpt: Option<&String>) {

        if let Some(text) = textOpt {

            if !text.trim().is_empty() {
                println!("\n{}:", console::style(label).bold());
                PrintWrappedText(text, 2, 80);
            }

        }

    }

    print_text_block_summary("Description", licenseEntry.description.as_ref());

    let parsedRules = &licenseEntry.info_components.parsed_rules;

    for (catName, color, rulesList) in [
        ("Permissions", "green", &parsedRules.permissions),
        ("Conditions", "yellow", &parsedRules.conditions),
        ("Limitations", "red", &parsedRules.limitations),
    ] {

        if !rulesList.is_empty() {
            println!("\n{}:", console::style(catName).bold().paint(color));

            for ruleDetail in rulesList {
                println!("  - {} ({})",
                    console::style(&ruleDetail.label).bold().paint(color),
                    console::style(&ruleDetail.tag).dim()
                );
            }

        }

    }

    print_text_block_summary("Note", licenseEntry.info_components.note_text.as_ref());

    let placeholderMapCliArgs = PLACEHOLDER_TO_ARG_MAP_TUPLES.iter().cloned().collect::<std::collections::HashMap<_,_>>();
    let rawPhToStdKeyMap = RAW_PLACEHOLDER_TO_STANDARD_KEY_TUPLES.iter().cloned().collect::<std::collections::HashMap<_,_>>();
    let cliArgToCacheKeyMap = CLI_ARG_TO_CACHE_KEY_TUPLES.iter().cloned().collect::<std::collections::HashMap<_,_>>();


    let fieldsDataContent: Option<FieldsDataContent> = cache.data_files
        .get(crate::constants::FIELDS_YML_KEY)
        .and_then(|entry| serde_yaml::from_value(entry.content.clone()).ok());


    if !licenseEntry.placeholders_in_body.is_empty() {
        println!("\n{}", console::style("Placeholder Values Used:").bold());

        for phFullStr in &licenseEntry.placeholders_in_body {
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

                        if let crate::cli::Commands::License(licArgs) = cmd {
                            licArgs.year.is_some()
                        }

                        else { false }

                    });
                    sourceInfo = if yearArgExplicitlyPassed {
                        console::style("CLI argument (--year)").cyan().to_string()
                    }

                    else {
                        console::style("Defaulted (current year)").blue().to_string()
                    };
                }

                else if userProvidedForFilling.contains_key(*standardKey) &&
                          cliArgToCacheKeyMap.values().any(|&vStdKey| vStdKey == *standardKey) &&
                          cliAllArgs.command.as_ref().map_or(false, |cmd| {

                              if let crate::cli::Commands::License(licArgs) = cmd {

                                  match *standardKey {
                                      "fullname" => licArgs.fullname.is_some(),
                                      "project" => licArgs.project.is_some(),
                                      "email" => licArgs.email.is_some(),
                                      "projecturl" => licArgs.projecturl.is_some(),
                                      _ => false,
                                  }

                              }

                              else { false }

                          })
                {
                    let cliArgName = placeholderMapCliArgs.get(phLower.as_str()).unwrap_or(&"CLI arg");
                    sourceInfo = console::style(format!("CLI argument ({})", cliArgName)).cyan().to_string();
                }

                else if cachedPlaceholdersAtStart.contains_key(*standardKey) {
                    sourceInfo = console::style("Saved preference (cache)").yellow().to_string();
                }

                else {
                    sourceInfo = console::style("Not specified").red().to_string();

                    if filledLicenseBody.contains(phFullStr) {
                        sourceInfo.push_str(&console::style(" (remains in file!)").red().bold().to_string());
                    }

                    valueUsedStr = "".to_string();
                }

            }
            else {
                sourceInfo = console::style("Unknown placeholder").magenta().to_string();

                if filledLicenseBody.contains(phFullStr) {
                    sourceInfo.push_str(&console::style(" (remains in file!)").red().bold().to_string());
                }

                valueUsedStr = "".to_string();
            }

            println!("  - {}: {}{}", console::style(phFullStr).magenta().bold(), sourceInfo, valueUsedStr);
        }

    }

    else {
        println!("\n{}: {}", console::style("Placeholder Values Used").bold(), console::style("(No standard placeholders in template)").dim());
    }
}