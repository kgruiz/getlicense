# -*- coding: utf-8 -*-
import argparse
import base64
import json  # For caching
import os
import re
import textwrap  # For formatting descriptions
from datetime import datetime
from pathlib import Path

import requests
import yaml  # Requires PyYAML

# --- Constants ---
GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"
CACHE_FILENAME: str = "license_cache.json"

# --- Map common placeholders to command-line arguments ---
PLACEHOLDER_TO_ARG_MAP: dict[str, str] = {
    "year": "--year",
    "fullname": "--fullname",
    "project": "--project",
    "email": "--email",
    "projecturl": "--projecturl",
    "yyyy": "--year",  # Handle Apache's format
    "name of copyright owner": "--fullname",  # Handle Apache's format
}

# --- GitHub API and Fetching Functions (Mostly Unchanged) ---


def GetGithubApi(endpoint: str) -> dict | list | None:
    """Makes a GET request to the GitHub API."""
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    url: str = f"{GITHUB_API_URL}{endpoint}"
    try:
        response = requests.get(url, headers=headers, timeout=15)  # Add timeout
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        print(f"Error: Timeout while fetching from GitHub API ({url})")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching from GitHub API ({url}): {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            print(f"Response Body: {e.response.text[:500]}...")
            if e.response.status_code == 403:
                rate_limit_info = e.response.headers.get("X-RateLimit-Remaining", "N/A")
                print(
                    f"Hint: Check GitHub API rate limits (Remaining: {rate_limit_info}) or authentication (set GITHUB_TOKEN)."
                )
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API call: {e}")
        return None


def FetchLicenseListFromApi() -> list[dict[str, str]] | None:
    """Fetches the list of license files *directly* from the GitHub repo API."""
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{LICENSES_PATH}?ref={BRANCH}"
    data = GetGithubApi(endpoint)
    if not data or not isinstance(data, list):
        print(f"Could not fetch or parse directory listing for {LICENSES_PATH}")
        return None

    licenseFiles: list[dict[str, str]] = []
    for item in data:
        # Ensure item is a dict and has necessary keys before accessing
        if (
            isinstance(item, dict)
            and item.get("type") == "file"
            and item.get("name", "").endswith(".txt")
        ):
            # Check for download_url, prefer it. Fallback might be needed if API changes.
            download_url = item.get("download_url")
            if not download_url:
                print(
                    f"Warning: No download_url found for {item.get('name', 'unknown file')}, attempting to construct."
                )
                # Potentially construct raw URL, but download_url is more reliable
                # download_url = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}/{item.get('path')}"
                # For now, skip if no download_url
                print(
                    f"Skipping {item.get('name', 'unknown file')} due to missing download_url."
                )
                continue

            licenseFiles.append(
                {
                    "name": item.get("name", "unknown.txt"),
                    "path": item.get("path", ""),
                    "sha": item.get("sha", ""),  # Git blob SHA
                    "download_url": download_url,
                }
            )
    return licenseFiles


def FetchLicenseContent(downloadUrl: str) -> str | None:
    """Fetches the content of a single license file."""
    try:
        response = requests.get(downloadUrl, timeout=10)  # Add timeout
        response.raise_for_status()
        return response.text
    except requests.exceptions.Timeout:
        print(f"Error: Timeout fetching license content from {downloadUrl}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching license content from {downloadUrl}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred fetching content: {e}")
        return None


# --- Parsing Function (Mostly Unchanged, returns full data) ---


def ParseLicenseData(filename: str, fileContent: str) -> dict[str, any] | None:
    """
    Parses SPDX ID, full front matter, and body from license file content.
    Returns dict including 'spdx_id', 'front_matter', 'body', or None.
    """
    spdxId: str | None = None
    frontMatter: dict[str, any] = {}
    body: str = fileContent.strip()

    if fileContent.strip().startswith("---"):
        parts = fileContent.split("---", 2)
        if len(parts) >= 3:
            frontMatterRaw: str = parts[1].strip()
            body = parts[2].strip()
            try:
                frontMatter = yaml.safe_load(frontMatterRaw) or {}
                if not isinstance(frontMatter, dict):
                    print(
                        f"Warning: Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}
                    match = re.search(
                        r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                    )
                    if match:
                        spdxId = match.group(1).strip()
                else:
                    spdxId = frontMatter.get("spdx-id")
            except yaml.YAMLError as e:
                print(f"Warning: YAML parse error for {filename}: {e}. Fallback.")
                frontMatter = {}
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()
        else:
            print(f"Warning: Malformed front matter in {filename}.")
            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)
    else:
        print(f"Warning: No front matter '---' in {filename}.")
        spdxId = GuessSpdxFromFilename(filename)

    # Ensure basic fields exist in frontMatter for consistency
    if not spdxId and "spdx-id" in frontMatter:
        spdxId = frontMatter["spdx-id"]  # Get from parsed FM if fallback missed
    if not spdxId:
        print(f"Error: Could not determine SPDX ID for {filename}. Skipping.")
        return None

    frontMatter.setdefault("spdx-id", spdxId)
    frontMatter.setdefault("title", spdxId)  # Fallback title

    return {"spdx_id": spdxId, "front_matter": frontMatter, "body": body}


def GuessSpdxFromFilename(filename: str) -> str | None:
    """Guesses SPDX ID from filename."""
    spdxIdGuess: str = os.path.splitext(filename)[0]  # Keep case initially
    # Basic check, could be improved (SPDX allows '+')
    if re.match(r"^[A-Za-z0-9.-]+$", spdxIdGuess):
        return spdxIdGuess  # Return the guess
    else:
        print(f"Warning: Could not reliably guess SPDX ID for {filename}")
        return None


# --- Caching Functions ---


def LoadCache(cacheFilePath: Path) -> dict[str, dict[str, any]]:
    """Loads the license cache from a JSON file."""
    if cacheFilePath.exists():
        try:
            with open(cacheFilePath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            print(
                f"Warning: Could not load cache file {cacheFilePath}: {e}. Starting fresh."
            )
    return {}


def SaveCache(cacheFilePath: Path, cacheData: dict[str, dict[str, any]]) -> None:
    """Saves the license cache to a JSON file."""
    try:
        cacheFilePath.parent.mkdir(
            parents=True, exist_ok=True
        )  # Ensure directory exists
        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
    except IOError as e:
        print(f"Error: Could not save cache file {cacheFilePath}: {e}")


def UpdateAndLoadLicenseCache(
    cacheFilePath: Path, forceRefresh: bool = False
) -> dict[str, dict[str, any]]:
    """
    Loads cache, checks GitHub for updates, fetches necessary files,
    updates cache, saves cache, and returns the up-to-date license data (basic info).
    """
    print("Loading cache...")
    cachedLicenses = LoadCache(cacheFilePath) if not forceRefresh else {}
    if forceRefresh:
        print("Cache refresh forced.")

    print("Fetching current license list from GitHub...")
    githubFiles = FetchLicenseListFromApi()
    if githubFiles is None:
        print("Error: Failed to fetch list from GitHub. Using potentially stale cache.")
        return cachedLicenses  # Return whatever we loaded

    currentGithubFiles = {item["name"]: item for item in githubFiles}
    updatedCache = {}
    needsSave = False

    # Process files currently on GitHub
    filesToFetchContent = []
    for name, ghInfo in currentGithubFiles.items():
        cachedEntry = None
        # Find corresponding cached entry (might need case-insensitive search if keys changed)
        # For simplicity, assume filename is consistent key reference for now
        # A more robust way might involve iterating cache keys if spdx id changed
        for key, val in cachedLicenses.items():
            if val.get("filename") == name:
                cachedEntry = val
                cached_spdx_lower = key
                break

        gh_sha = ghInfo.get("sha")
        if not gh_sha:
            print(
                f"Warning: No SHA found for {name} from GitHub API. Assuming changed."
            )
            filesToFetchContent.append(ghInfo)
            needsSave = True
            continue

        if cachedEntry and cachedEntry.get("sha") == gh_sha:
            # SHA matches, use cached basic info
            updatedCache[cached_spdx_lower] = cachedEntry
        else:
            # New file or changed file, mark for fetching content
            if cachedEntry:
                print(f"  Detected change in {name} (SHA mismatch).")
            else:
                print(f"  Detected new file {name}.")
            filesToFetchContent.append(ghInfo)
            needsSave = True  # Cache will be updated

    # Identify deleted files
    cachedNames = {
        entry.get("filename")
        for entry in cachedLicenses.values()
        if entry.get("filename")
    }
    deletedNames = cachedNames - set(currentGithubFiles.keys())
    if deletedNames:
        print(f"  Detected deleted files: {', '.join(deletedNames)}")
        needsSave = True
        # The deleted files are implicitly removed as we build updatedCache only from currentGithubFiles

    # Fetch content for new/changed files
    if filesToFetchContent:
        print(
            f"Fetching content for {len(filesToFetchContent)} new/updated license files..."
        )
        for ghInfo in filesToFetchContent:
            print(f"  Fetching {ghInfo['name']}...")
            content = FetchLicenseContent(ghInfo["download_url"])
            if content:
                parsedData = ParseLicenseData(ghInfo["name"], content)
                if parsedData:
                    # Store basic info + filename + sha in cache
                    spdx_lower = parsedData["spdx_id"].lower()
                    updatedCache[spdx_lower] = {
                        "spdx_id": parsedData["spdx_id"],
                        "title": parsedData["front_matter"].get(
                            "title", parsedData["spdx_id"]
                        ),
                        "filename": ghInfo["name"],
                        "sha": ghInfo.get("sha", ""),  # Store the new SHA
                        # DO NOT store 'body' or full 'front_matter' in the main cache
                    }
            else:
                print(
                    f"  Failed to fetch/parse {ghInfo['name']}. It won't be in the updated cache."
                )
                # If it was previously cached, it might be removed implicitly
                # Or explicitly remove if needed: updatedCache.pop(cached_spdx_lower, None)

    if needsSave:
        print("Saving updated cache...")
        SaveCache(cacheFilePath, updatedCache)
    else:
        print("Cache is up-to-date.")

    return updatedCache


# --- Display and Filling Functions (Unchanged except DisplayLicenseInfo needs fetch) ---


def FindPlaceholders(templateBody: str) -> set[str]:
    """Finds all unique placeholders like [placeholder] in the text."""
    pattern: str = r"\[([^\]]+)\]"
    placeholders: list[str] = re.findall(pattern, templateBody)
    return set(placeholders)


def FillLicenseTemplate(templateBody: str, replacements: dict[str, str]) -> str:
    """Fills placeholders in the license template body."""
    filledText: str = templateBody
    for placeholder, value in replacements.items():
        phFormatted: str = f"[{placeholder.strip('[]')}]"
        filledText = filledText.replace(phFormatted, str(value))
    return filledText


def ListLicenses(licensesData: dict[str, dict[str, any]]) -> None:
    """Prints a list of available licenses from cached data."""
    if not licensesData:
        print("No licenses found in cache.")
        return
    print("\nAvailable Licenses (SPDX ID: Title):")
    print("-" * 50)
    sortedIds: list[str] = sorted(licensesData.keys())
    for spdxLower in sortedIds:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        print(f"  {spdx:<25} : {title}")
    print("-" * 50)


def DisplayLicenseInfo(
    spdxIdLower: str, licensesData: dict[str, dict[str, any]]
) -> None:
    """Fetches full data if needed and prints formatted metadata."""
    basicInfo = licensesData.get(spdxIdLower)
    if not basicInfo:
        print(f"Error: Basic info for {spdxIdLower.upper()} not found in cache.")
        return

    filename = basicInfo.get("filename")
    if not filename:
        print(f"Error: Filename missing for {spdxIdLower.upper()} in cache.")
        return

    # Construct download URL (needs path, which isn't cached - fetch list again or store path)
    # Let's refetch the list briefly to get the download URL - slightly inefficient but simple
    print(f"Fetching download URL for {filename}...")
    githubFiles = FetchLicenseListFromApi()  # Re-fetch list to get current URLs
    downloadUrl = None
    if githubFiles:
        for item in githubFiles:
            if item.get("name") == filename:
                downloadUrl = item.get("download_url")
                break

    if not downloadUrl:
        print(
            f"Error: Could not find download URL for {filename}. Cannot fetch full info."
        )
        return

    print(f"Fetching full content for {filename}...")
    content = FetchLicenseContent(downloadUrl)
    if not content:
        print(f"Error: Failed to fetch full content for {filename}.")
        return

    fullLicenseData = ParseLicenseData(filename, content)
    if not fullLicenseData:
        print(f"Error: Failed to parse full content for {filename}.")
        return

    # Now display using the fetched full data
    fm: dict[str, any] = fullLicenseData.get("front_matter", {})
    spdxId: str = fullLicenseData.get("spdx_id", "N/A")
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

    placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
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


# --- Main Execution ---


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, display info for, or fill open source license templates from github/choosealicense.com using local caching.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --list                      List all available licenses (uses cache).
  %(prog)s --refresh --list            Force refresh cache then list licenses.
  %(prog)s --info MIT                  Show detailed info (fetches full content).
  %(prog)s --show-placeholders NCSA    Show placeholders (fetches full content).
  %(prog)s --license MIT -f "Jane Doe" Fill license (fetches full content).
  %(prog)s -l Apache-2.0 -f ACME -o LIC Fill license, output to file LIC (fetches full content).
""",
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh of the local license cache from GitHub.",
    )
    parser.add_argument(
        "--cache-file",
        default=CACHE_FILENAME,
        help=f"Path to the license cache file (default: {CACHE_FILENAME}).",
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
        help="List available licenses from cache and exit.",
    )
    actionGroup.add_argument(
        "--info",
        metavar="LICENSE_ID",
        help="Show detailed metadata (fetches full content).",
    )
    actionGroup.add_argument(
        "--show-placeholders",
        metavar="LICENSE_ID",
        help="Show placeholders (fetches full content).",
    )

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
        "-o", "--output", help="Output file path. Defaults to printing to stdout."
    )

    args = parser.parse_args()
    cacheFilePath = Path(args.cache_file)

    # Update cache if needed, then load basic license info
    licensesData = UpdateAndLoadLicenseCache(cacheFilePath, args.refresh)
    if not licensesData:
        print("\nOperation failed: Could not load or update license data.")
        return 1

    # --- Handle Actions ---

    if args.list:
        ListLicenses(licensesData)
        return 0

    if args.info:
        requestedIdLower: str = args.info.lower()
        if requestedIdLower not in licensesData:
            print(f"\nError: License '{args.info}' not found in cache.")
            print("Use --list to see available licenses or --refresh to update cache.")
            return 1
        DisplayLicenseInfo(
            requestedIdLower, licensesData
        )  # Fetches full content inside
        return 0

    if args.show_placeholders:
        requestedIdLower = args.show_placeholders.lower()
        basicInfo = licensesData.get(requestedIdLower)
        if not basicInfo:
            print(f"\nError: License '{args.show_placeholders}' not found in cache.")
            print("Use --list or --refresh.")
            return 1

        filename = basicInfo.get("filename")
        if not filename:
            print(f"Error: Filename missing for {requestedIdLower.upper()} in cache.")
            return 1

        # Fetch full content to find placeholders accurately
        print(f"Fetching download URL for {filename}...")
        githubFiles = FetchLicenseListFromApi()
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            print(
                f"Error: Could not find download URL for {filename}. Cannot fetch body."
            )
            return 1

        print(f"Fetching full content for {filename} to find placeholders...")
        content = FetchLicenseContent(downloadUrl)
        if not content:
            print(f"Error: Failed to fetch full content for {filename}.")
            return 1
        fullLicenseData = ParseLicenseData(filename, content)  # Parse again to get body
        if not fullLicenseData:
            print(f"Error: Failed to parse full content for {filename}.")
            return 1

        print(
            f"\nPlaceholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}):"
        )
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
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
        requestedLicenseIdLower: str = args.license.lower()
        basicInfo = licensesData.get(requestedLicenseIdLower)
        if not basicInfo:
            print(f"\nError: License '{args.license}' not found in cache.")
            print("Use --list or --refresh.")
            return 1

        filename = basicInfo.get("filename")
        if not filename:
            print(
                f"Error: Filename missing for {requestedLicenseIdLower.upper()} in cache."
            )
            return 1

        # Fetch full content for filling
        print(f"Fetching download URL for {filename}...")
        githubFiles = FetchLicenseListFromApi()
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            print(
                f"Error: Could not find download URL for {filename}. Cannot fetch body."
            )
            return 1

        print(f"Fetching full content for {filename} to fill template...")
        content = FetchLicenseContent(downloadUrl)
        if not content:
            print(f"Error: Failed to fetch full content for {filename}.")
            return 1
        fullLicenseData = ParseLicenseData(filename, content)
        if not fullLicenseData:
            print(f"Error: Failed to parse full content for {filename}.")
            return 1

        title: str = fullLicenseData.get("front_matter", {}).get("title", "N/A")
        spdxId: str = fullLicenseData.get("spdx_id", "N/A")
        body: str = fullLicenseData.get("body", "")

        print(f"\nUsing license: {title} ({spdxId})")

        # --- Prepare replacements (Same as before) ---
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

        # --- Check Placeholders (Same as before) ---
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
                    f"  Warning: Placeholder [{ph}] found, but no value provided via {argSuggestion}."
                )
                missingArgs = True
        if missingArgs:
            print("  License generated, but placeholders might remain unfilled.")

        # --- Fill and Output (Same as before) ---
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

    # If no action argument was given
    print(
        "\nError: No action specified. Use --list, --info, --show-placeholders, or --license."
    )
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
