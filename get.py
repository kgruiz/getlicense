import argparse
import base64
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path

import requests
import yaml

GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"

PLACEHOLDER_TO_ARG_MAP: dict[str, str] = {
    "year": "--year",
    "fullname": "--fullname",
    "project": "--project",
    "email": "--email",
    "projecturl": "--projecturl",
    "yyyy": "--year",  # Handle Apache's format
    "name of copyright owner": "--fullname",  # Handle Apache's format
}


def GetGithubApi(endpoint: str) -> dict[str, any] | None:
    """Makes a GET request to the GitHub API.

    Parameters
    ----------
    endpoint : str
        The API endpoint to request.

    Returns
    -------
    dict[str, any] | None
        The JSON response from the API, or None if an error occurred.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}

    url: str = f"{GITHUB_API_URL}{endpoint}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching from GitHub API ({url}): {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            print(
                f"Response Body: {e.response.text[:500]}..."
            )  # Print part of the body
            if e.response.status_code == 403:
                print(
                    "Hint: Check GitHub API rate limits or authentication (set GITHUB_TOKEN environment variable)."
                )
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API call: {e}")
        return None


def FetchLicenseList() -> list[dict[str, str]] | None:
    """Fetches the list of license files from the GitHub repo.

    Returns
    -------
    list[dict[str, str]] | None
        A list of dictionaries containing license file information, or None if an error occurred.
    """
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{LICENSES_PATH}?ref={BRANCH}"
    data = GetGithubApi(endpoint)
    if not data or not isinstance(data, list):
        print(f"Could not fetch or parse directory listing for {LICENSES_PATH}")
        return None

    licenseFiles: list[dict[str, str]] = []
    for item in data:

        if item.get("type") == "file" and item.get("name", "").endswith(".txt"):
            licenseFiles.append(
                {
                    "name": item["name"],
                    "path": item["path"],
                    "download_url": item.get(
                        "download_url"
                    ),  # Use download_url directly
                }
            )
    return licenseFiles


def FetchLicenseContent(downloadUrl: str) -> str | None:
    """Fetches and decodes the content of a single license file.

    Parameters
    ----------
    downloadUrl : str
        The URL to download the license content from.

    Returns
    -------
    str | None
        The content of the license file as a string, or None if an error occurred.
    """
    try:
        response = requests.get(downloadUrl)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching license content from {downloadUrl}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred fetching content: {e}")
        return None


def ParseLicenseData(filename: str, fileContent: str) -> dict[str, any] | None:
    """Parses SPDX ID, full front matter, and body from license file content.

    Parameters
    ----------
    filename : str
        The name of the license file.
    fileContent : str
        The content of the license file.

    Returns
    -------
    dict[str, any] | None
        A dictionary containing the SPDX ID, front matter, and body of the license, or None if the SPDX ID is missing.
    """
    spdxId: str | None = None
    frontMatter: dict[str, any] = {}
    body: str = fileContent.strip()  # Default to full content

    if fileContent.strip().startswith("---"):
        parts = fileContent.split("---", 2)
        if len(parts) >= 3:  # Should be exactly 3 parts: '', front_matter, body
            frontMatterRaw: str = parts[1].strip()
            body = parts[2].strip()
            try:
                frontMatter = (
                    yaml.safe_load(frontMatterRaw) or {}
                )  # Ensure it's a dict, even if empty/invalid
                if not isinstance(frontMatter, dict):
                    print(
                        f"Warning: Front matter in {filename} parsed but is not a dictionary. Trying fallback."
                    )
                    frontMatter = {}  # Reset if not dict
                    # Fallback attempt for SPDX ID if YAML isn't dict
                    match = re.search(
                        r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                    )
                    if match:
                        spdxId = match.group(1).strip()
                else:
                    spdxId = frontMatter.get("spdx-id")

            except yaml.YAMLError as e:
                print(
                    f"Warning: Could not parse YAML front matter for {filename}: {e}. Trying fallback."
                )
                frontMatter = {}  # Reset on YAML error
                # Fallback regex search if YAML fails
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()
                # Could add regex for title etc. here too if needed as fallback

        else:
            print(
                f"Warning: Malformed front matter structure in {filename}. Using full content."
            )
            # Fallback: Guess SPDX ID from filename if needed
            if not spdxId:
                spdxIdGuess: str = os.path.splitext(filename)[0].upper()
                if re.match(r"^[A-Z0-9.-]+$", spdxIdGuess):
                    spdxId = spdxIdGuess
                else:
                    print(f"Warning: Could not reliably guess SPDX ID for {filename}")
    else:
        print(
            f"Warning: No front matter '---' found in {filename}. Using full content."
        )
        # Fallback: Guess SPDX ID from filename
        spdxIdGuess = os.path.splitext(filename)[0].upper()
        if re.match(r"^[A-Z0-9.-]+$", spdxIdGuess):
            spdxId = spdxIdGuess
        else:
            print(f"Warning: Could not reliably guess SPDX ID for {filename}")

    # Fill in missing basic fields if possible
    if "spdx-id" not in frontMatter and spdxId:
        frontMatter["spdx-id"] = spdxId
    if "title" not in frontMatter:
        frontMatter["title"] = (
            spdxId if spdxId else "Unknown Title"
        )  # Use SPDX as fallback title

    if spdxId:
        return {"spdx_id": spdxId, "front_matter": frontMatter, "body": body}
    else:
        print(f"Warning: Could not determine SPDX ID for {filename}. Skipping.")
        return None


def FetchAndParseAllLicenses() -> dict[str, dict[str, any]] | None:
    """Fetches and parses all license files from GitHub.

    Returns
    -------
    dict[str, dict[str, any]] | None
        A dictionary containing all license data, or None if an error occurred.
    """
    print("Fetching license list from GitHub...")
    licenseFiles = FetchLicenseList()
    if not licenseFiles:
        return None

    print(f"Found {len(licenseFiles)} potential license files. Fetching content...")
    allLicensesData: dict[str, dict[str, any]] = {}
    fetchedCount: int = 0
    for fileInfo in licenseFiles:

        if not fileInfo.get("download_url"):
            print(f"Warning: No download URL for {fileInfo['name']}. Skipping.")
            continue

        content = FetchLicenseContent(fileInfo["download_url"])
        if content:
            fetchedCount += 1
            # print(f"  Fetched {fileInfo['name']}...") # Verbose
            parsedData = ParseLicenseData(fileInfo["name"], content)
            if parsedData:
                allLicensesData[parsedData["spdx_id"].lower()] = parsedData
        else:
            print(f"  Failed to fetch content for {fileInfo['name']}.")

    print(
        f"Successfully fetched and parsed content for {len(allLicensesData)} licenses."
    )
    if fetchedCount < len(licenseFiles):
        print(
            f"Warning: Could not fetch content for {len(licenseFiles) - fetchedCount} files."
        )

    return allLicensesData


# --- Display and Filling Functions ---


def FindPlaceholders(templateBody: str) -> set[str]:
    """Finds all unique placeholders like [placeholder] in the text.

    Parameters
    ----------
    templateBody : str
        The text to search for placeholders.

    Returns
    -------
    set[str]
        A set of unique placeholders found in the text.
    """
    pattern: str = r"\[([^\]]+)\]"
    placeholders: list[str] = re.findall(pattern, templateBody)
    return set(placeholders)


def FillLicenseTemplate(templateBody: str, replacements: dict[str, str]) -> str:
    """Fills placeholders in the license template body.

    Parameters
    ----------
    templateBody : str
        The license template body.
    replacements : dict[str, str]
        A dictionary of placeholders and their corresponding values.

    Returns
    -------
    str
        The filled license template.
    """
    filledText: str = templateBody
    for placeholder, value in replacements.items():
        phFormatted: str = f"[{placeholder.strip('[]')}]"
        filledText = filledText.replace(phFormatted, str(value))
    return filledText


def ListLicenses(licensesData: dict[str, dict[str, any]]) -> None:
    """Prints a list of available licenses.

    Parameters
    ----------
    licensesData : dict[str, dict[str, any]]
        A dictionary containing license data.
    """
    if not licensesData:
        print("No licenses found or loaded.")
        return
    print("\nAvailable Licenses (SPDX ID: Title):")
    print("-" * 50)
    sortedIds: list[str] = sorted(licensesData.keys())
    for spdxLower in sortedIds:
        data = licensesData[spdxLower]
        # Use .get with fallback for safety, though parse_license_data tries to ensure these exist
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("front_matter", {}).get("title", "N/A")
        print(f"  {spdx:<25} : {title}")  # Format for alignment
    print("-" * 50)


def DisplayLicenseInfo(licenseData: dict[str, any]) -> None:
    """Prints the formatted metadata for a license.

    Parameters
    ----------
    licenseData : dict[str, any]
        A dictionary containing license data.
    """
    fm: dict[str, any] = licenseData.get("front_matter", {})
    spdxId: str = licenseData.get("spdx_id", "N/A")
    title: str = fm.get("title", "N/A")

    print(f"\n--- License Information: {title} ({spdxId}) ---")

    if fm.get("nickname"):
        print(f"\nNickname: {fm['nickname']}")

    if fm.get("description"):
        print("\nDescription:")
        print(
            textwrap.fill(
                fm["description"], width=78, initial_indent="  ", subsequent_indent="  "
            )
        )

    if fm.get("how"):
        print("\nHow to Apply:")
        print(
            textwrap.fill(
                fm["how"], width=78, initial_indent="  ", subsequent_indent="  "
            )
        )

    # Helper to print rule lists
    def PrintRules(label: str, key: str) -> None:
        if fm.get(key) and isinstance(fm[key], list):
            print(f"\n{label}:")
            for item in fm[key]:
                print(f"  - {item}")

    PrintRules("Permissions", "permissions")
    PrintRules("Conditions", "conditions")
    PrintRules("Limitations", "limitations")

    if fm.get("using") and isinstance(fm["using"], dict):
        print("\nNotable Projects Using This License:")
        for project, url in fm["using"].items():
            print(f"  - {project}: {url}")

    if fm.get("note"):
        print("\nNote:")
        print(
            textwrap.fill(
                fm["note"], width=78, initial_indent="  ", subsequent_indent="  "
            )
        )

    # Display placeholders found in the body
    placeholders = FindPlaceholders(licenseData.get("body", ""))
    if placeholders:
        print("\nPlaceholders in Body:")
        for ph in sorted(list(placeholders)):
            argSuggestion: str = PLACEHOLDER_TO_ARG_MAP.get(
                ph.lower(), f"(no direct argument for '[{ph}]')"
            )
            defaultInfo: str = ""
            if ph.lower() in ["year", "yyyy"]:
                defaultInfo = " (defaults to current year)"
            print(f"  - [{ph}] (Argument: {argSuggestion}{defaultInfo})")
    else:
        print("\nPlaceholders in Body: (None detected)")

    print("\n--- End License Information ---")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, display info for, or fill open source license templates from github/choosealicense.com.",
        formatter_class=argparse.RawDescriptionHelpFormatter,  # Keep formatting in help
        epilog="""Examples:
  %(prog)s --list                      List all available licenses.
  %(prog)s --info MIT                  Show detailed info about the MIT license.
  %(prog)s --show-placeholders NCSA    Show only placeholders for the NCSA license.
  %(prog)s --license MIT -f "Jane Doe" Display the filled MIT license text.
  %(prog)s -l Apache-2.0 -f ACME -o LIC Display filled Apache-2.0 license, output to file LIC.
""",
    )

    actionGroup = parser.add_mutually_exclusive_group()
    actionGroup.add_argument(
        "-l",
        "--license",
        help="SPDX ID of the license template to fill (case-insensitive).",
    )
    actionGroup.add_argument(
        "--list",
        action="store_true",
        help="List available licenses found in the GitHub repo and exit.",
    )
    actionGroup.add_argument(
        "--info",
        metavar="LICENSE_ID",
        help="Show detailed metadata information about the specified license and exit.",
    )
    actionGroup.add_argument(
        "--show-placeholders",
        metavar="LICENSE_ID",
        help="Show only the placeholders found in the specified license template and exit.",
    )

    # Arguments for filling placeholders (only relevant if --license is used)
    fillGroup = parser.add_argument_group(
        "Options for filling placeholders (used with --license)"
    )
    fillGroup.add_argument(
        "-f", "--fullname", help="Full name of the copyright holder."
    )
    fillGroup.add_argument(
        "-y", "--year", help="Copyright year. Defaults to current year."
    )
    fillGroup.add_argument("-p", "--project", help="Project name.")
    fillGroup.add_argument("-e", "--email", help="Email address.")
    fillGroup.add_argument("-u", "--projecturl", help="Project URL.")
    fillGroup.add_argument(
        "-o",
        "--output",
        help="Output file path to save the filled license. Defaults to printing to stdout.",
    )

    args = parser.parse_args()

    # Fetch and parse licenses from GitHub - do this once for all actions
    licensesData = FetchAndParseAllLicenses()
    if licensesData is None or not licensesData:
        print("\nFailed to fetch or parse licenses from GitHub.")
        return 1  # Indicate error

    if args.list:
        ListLicenses(licensesData)
        return 0

    if args.info:
        requestedIdLower: str = args.info.lower()
        licenseInfoData = licensesData.get(requestedIdLower)
        if not licenseInfoData:
            print(f"\nError: License '{args.info}' not found for showing info.")
            print("Use --list to see available licenses.")
            return 1
        DisplayLicenseInfo(licenseInfoData)
        return 0

    if args.show_placeholders:
        requestedIdLower = args.show_placeholders.lower()
        licenseInfoData = licensesData.get(requestedIdLower)
        if not licenseInfoData:
            print(
                f"\nError: License '{args.show_placeholders}' not found for showing placeholders."
            )
            print("Use --list to see available licenses.")
            return 1
        print(
            f"\nPlaceholders for {licenseInfoData.get('front_matter',{}).get('title','N/A')} ({licenseInfoData.get('spdx_id','N/A')}):"
        )
        placeholders = FindPlaceholders(licenseInfoData.get("body", ""))
        if not placeholders:
            print("  (No standard [placeholder] patterns found)")
        else:
            for ph in sorted(list(placeholders)):
                argSuggestion = PLACEHOLDER_TO_ARG_MAP.get(
                    ph.lower(), f"(no direct argument for '[{ph}]')"
                )
                defaultInfo = ""
                if ph.lower() in ["year", "yyyy"]:
                    defaultInfo = " (defaults to current year if not provided)"
                print(f"  - [{ph}]  --> Use argument: {argSuggestion}{defaultInfo}")
        return 0

    if args.license:

        # --- Proceed with filling a license ---
        requestedLicenseIdLower: str = args.license.lower()
        licenseInfoData = licensesData.get(requestedLicenseIdLower)

        if not licenseInfoData:
            print(f"\nError: License with SPDX ID '{args.license}' not found.")
            print("Use --list to see available licenses.")
            return 1

        title: str = licenseInfoData.get("front_matter", {}).get("title", "N/A")
        spdxId: str = licenseInfoData.get("spdx_id", "N/A")
        body: str = licenseInfoData.get("body", "")

        print(f"\nUsing license: {title} ({spdxId})")

        # --- Prepare replacements ---
        currentYear: str = str(datetime.now().year)
        replacements: dict[str, str] = {}
        defaultYear: str = args.year if args.year else currentYear
        replacements["year"] = defaultYear
        replacements["yyyy"] = defaultYear
        if args.fullname:
            replacements["fullname"] = args.fullname
            replacements["name of copyright owner"] = args.fullname
        if args.project:
            replacements["project"] = args.project
        if args.email:
            replacements["email"] = args.email
        if args.projecturl:
            replacements["projecturl"] = args.projecturl

        # Check if all found placeholders have a replacement value
        foundPlaceholders = FindPlaceholders(body)
        missingArgs: bool = False
        print("Checking required placeholders:")
        for ph in foundPlaceholders:
            phCheckKey: str = ph.lower()
            if phCheckKey == "yyyy":
                phCheckKey = "year"
            if phCheckKey == "name of copyright owner":
                phCheckKey = "fullname"

            if phCheckKey not in replacements:
                argSuggestion: str = PLACEHOLDER_TO_ARG_MAP.get(ph.lower(), f"'[{ph}]'")
                print(
                    f"  Warning: Placeholder [{ph}] found in template, but no value provided using {argSuggestion}."
                )
                missingArgs = True

        if missingArgs:
            print(
                "  The license will be generated, but placeholders might remain unfilled."
            )

        filledLicense: str = FillLicenseTemplate(body, replacements)

        if args.output:
            try:
                outputPath: Path = Path(args.output)
                with open(outputPath, "w", encoding="utf-8") as f:

                    f.write(filledLicense)
                print(f"\nLicense successfully written to '{args.output}'")
            except IOError as e:
                print(f"\nError writing to output file '{args.output}': {e}")
                return 1
        else:
            print("\n--- Filled License Text ---")
            print(filledLicense)
            print("--- End License Text ---\n")
        return 0

    print(
        "\nError: No action specified. Use --list, --info, --show-placeholders, or --license."
    )
    parser.print_help()
    return 1


if __name__ == "__main__":

    import sys

    sys.exit(main())
