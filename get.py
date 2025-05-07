# -*- coding: utf-8 -*-
import argparse
import base64
import json  # For caching
import os
import re
import textwrap
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
DATA_PATH: str = "_data"
CACHE_FILENAME: str = "license_cache.json"

# --- Map standard placeholders to command-line arguments ---
# Based on fields.yml
PLACEHOLDER_TO_ARG_MAP: dict[str, str] = {
    "fullname": "--fullname",
    "login": "(no direct argument for '[login]')",
    "email": "--email",
    "project": "--project",
    "description": "(no direct argument for '[description]')",
    "year": "--year",
    "projecturl": "--projecturl",
    "yyyy": "--year",  # Handle Apache's format, maps to year
    "name of copyright owner": "--fullname",  # Handle Apache's format, maps to fullname
}

# --- Helper Functions (GitHub API, Fetching, Parsing) ---


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


def FetchGithubDirListing(repo_path: str) -> list[dict[str, object]] | None:
    """Fetches the list of files in a directory from the GitHub repo API."""
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{repo_path}?ref={BRANCH}"
    data = GetGithubApi(endpoint)
    if not data or not isinstance(data, list):
        print(f"Could not fetch or parse directory listing for {repo_path}")
        return None
    return data


def FetchFileContent(downloadUrl: str) -> str | None:
    """Fetches the content of a single file from a direct download URL."""
    try:
        response = requests.get(downloadUrl, timeout=10)  # Add timeout
        response.raise_for_status()
        return response.text
    except requests.exceptions.Timeout:
        print(f"Error: Timeout fetching content from {downloadUrl}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching content from {downloadUrl}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred fetching content: {e}")
        return None


# --- Parsing Functions ---


def ParseLicenseFile(filename: str, fileContent: str) -> dict[str, object] | None:
    """
    Parses SPDX ID, full front matter, and body from license file content.
    Returns dict including 'spdx_id', 'front_matter', 'body', or None.
    """
    spdxId: str | None = None
    frontMatter: dict[str, object] = {}
    body: str = fileContent.strip()

    if fileContent.strip().startswith("---"):
        parts = fileContent.split("---", 2)
        if len(parts) >= 3:
            frontMatterRaw: str = parts[1].strip()
            body = parts[2].strip()
            try:
                # Load YAML, allow empty results
                frontMatter = yaml.safe_load(frontMatterRaw) or {}
                if not isinstance(frontMatter, dict):
                    print(
                        f"Warning: Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}
                # Get SPDX ID from parsed data first
                spdxId = frontMatter.get("spdx-id")

            except yaml.YAMLError as e:
                print(f"Warning: YAML parse error for {filename}: {e}. Fallback.")
                frontMatter = {}
                # Fallback regex search if YAML fails
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()

        else:
            print(f"Warning: Malformed front matter in {filename}.")
            # Fallback: Guess SPDX ID from filename if needed
            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)
    else:
        print(f"Warning: No front matter '---' in {filename}.")
        spdxId = GuessSpdxFromFilename(filename)

    # Final check and fallback for SPDX ID
    if not spdxId and "spdx-id" in frontMatter:
        spdxId = frontMatter["spdx-id"]
    if not spdxId:
        print(f"Error: Could not determine SPDX ID for {filename}. Skipping.")
        return None

    # Ensure basic fields exist in frontMatter for consistency
    frontMatter.setdefault("spdx-id", spdxId)
    frontMatter.setdefault("title", spdxId)  # Fallback title

    # Ensure rules lists exist, even if empty, for caching
    frontMatter.setdefault("permissions", [])
    frontMatter.setdefault("conditions", [])
    frontMatter.setdefault("limitations", [])

    return {"spdx_id": spdxId, "front_matter": frontMatter, "body": body}


def GuessSpdxFromFilename(filename: str) -> str | None:
    """Guesses SPDX ID from filename (case-sensitive potential)."""
    spdxIdGuess: str = os.path.splitext(filename)[0]
    # Basic check, SPDX allows letters, numbers, ., -, +
    if re.match(r"^[A-Za-z0-9.\-\+]+$", spdxIdGuess):
        return spdxIdGuess
    else:
        print(
            f"Warning: Filename {filename} doesn't look like a typical SPDX ID format. Cannot reliably guess."
        )
        return None


def ParseDataFile(filename: str, fileContent: str) -> object | None:
    """Parses YAML data file content."""
    try:
        data = yaml.safe_load(fileContent)
        return data
    except yaml.YAMLError as e:
        print(f"Error parsing YAML data file {filename}: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred parsing data file: {e}")
        return None


# --- Caching Functions ---


def LoadCache(cacheFilePath: Path) -> dict[str, object]:
    """Loads the license cache from a JSON file."""
    if cacheFilePath.exists():
        try:
            with open(cacheFilePath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            print(
                f"Warning: Could not load cache file {cacheFilePath}: {e}. Starting fresh."
            )
            return {}  # Return empty cache on error
        except Exception as e:
            print(f"An unexpected error occurred loading cache: {e}")
            return {}
    return {}


def SaveCache(cacheFilePath: Path, cacheData: dict[str, object]) -> None:
    """Saves the license cache to a JSON file."""
    try:
        cacheFilePath.parent.mkdir(
            parents=True, exist_ok=True
        )  # Ensure directory exists
        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
    except IOError as e:
        print(f"Error: Could not save cache file {cacheFilePath}: {e}")
    except Exception as e:
        print(f"An unexpected error occurred saving cache: {e}")


def UpdateAndLoadLicenseCache(
    cacheFilePath: Path, forceRefresh: bool = False
) -> dict[str, object]:
    """
    Loads cache, checks GitHub for updates in licenses and data files,
    fetches necessary files, updates cache, saves cache, and returns the
    up-to-date cache data (basic license info and data files).
    """
    print("Loading cache...")
    cachedData = LoadCache(cacheFilePath) if not forceRefresh else {}
    if forceRefresh:
        print("Cache refresh forced.")

    needsSave = False
    processedDataFiles = {}  # Store processed data files separately

    # --- Update Data Files (_data/*.yml) ---
    dataFilesToFetch = []
    print(f"Fetching current data file list from GitHub ({DATA_PATH})...")
    githubDataFiles = FetchGithubDirListing(DATA_PATH)
    if githubDataFiles is None:
        print(
            "Warning: Failed to fetch data file list. Using potentially stale cached data."
        )
        # Extract existing data files from cache if possible
        for key, val in cachedData.items():
            if key.startswith("data:"):
                processedDataFiles[key] = val
    else:
        currentGithubDataFiles = {
            item["name"]: item
            for item in githubDataFiles
            if isinstance(item, dict)
            and item.get("type") == "file"
            and item.get("name").endswith(".yml")
        }

        for name, ghInfo in currentGithubDataFiles.items():
            cacheKey = f"data:{name}"
            cachedEntry = cachedData.get(cacheKey)
            gh_sha = ghInfo.get("sha")
            if not gh_sha:
                print(f"Warning: No SHA for data file {name}. Assuming changed.")
                dataFilesToFetch.append(ghInfo)
                needsSave = True
                continue

            if (
                cachedEntry
                and isinstance(cachedEntry, dict)
                and cachedEntry.get("sha") == gh_sha
            ):
                # SHA matches, use cached data file content
                processedDataFiles[cacheKey] = cachedEntry
            else:
                # New or changed data file
                if cachedEntry:
                    print(f"  Detected change in data file {name} (SHA mismatch).")
                else:
                    print(f"  Detected new data file {name}.")
                dataFilesToFetch.append(ghInfo)
                needsSave = True

        # Identify deleted data files
        cachedDataFilenames = {
            k.split(":", 1)[1] for k in cachedData.keys() if k.startswith("data:")
        }
        deletedDataFilenames = cachedDataFilenames - set(currentGithubDataFiles.keys())
        if deletedDataFilenames:
            print(f"  Detected deleted data files: {', '.join(deletedDataFilenames)}")
            # No need to explicitly remove, they just won't be added to processedDataFiles
            needsSave = True

    # Fetch content for new/changed data files
    if dataFilesToFetch:
        print(f"Fetching content for {len(dataFilesToFetch)} new/updated data files...")
        for ghInfo in dataFilesToFetch:
            print(f"  Fetching {ghInfo['name']}...")
            content = FetchFileContent(ghInfo["download_url"])
            if content:
                parsedData = ParseDataFile(ghInfo["name"], content)
                if parsedData is not None:
                    # Store parsed data and SHA in cache
                    processedDataFiles[f'data:{ghInfo["name"]}'] = {
                        "sha": ghInfo.get("sha", ""),
                        "content": parsedData,  # Store the parsed YAML data
                    }
            else:
                print(f"  Failed to fetch/parse data file {ghInfo['name']}.")

    # --- Update License Files (_licenses/*.txt) ---
    licenseFilesToFetch = []
    print(f"\nFetching current license file list from GitHub ({LICENSES_PATH})...")
    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)
    processedLicenses = {}  # Store processed license files separately

    if githubLicenseFiles is None:
        print(
            "Warning: Failed to fetch license file list. Using potentially stale cached licenses."
        )
        for key, val in cachedData.items():
            if not key.startswith("data:"):
                processedLicenses[key] = val  # Keep existing license entries
    else:
        currentGithubLicenseFiles = {
            item["name"]: item for item in githubLicenseFiles if isinstance(item, dict)
        }

        for name, ghInfo in currentGithubLicenseFiles.items():
            if not name.endswith(".txt"):
                continue  # Skip non-txt files

            cachedEntry = None
            cached_spdx_lower = None
            # Find corresponding cached entry by filename
            for key, val in cachedData.items():
                if (
                    not key.startswith("data:")
                    and isinstance(val, dict)
                    and val.get("filename") == name
                ):
                    cachedEntry = val
                    cached_spdx_lower = key
                    break

            gh_sha = ghInfo.get("sha")
            if not gh_sha:
                print(f"Warning: No SHA for license file {name}. Assuming changed.")
                licenseFilesToFetch.append(ghInfo)
                needsSave = True
                continue

            if cachedEntry and cachedEntry.get("sha") == gh_sha:
                # SHA matches, use cached basic info
                # Make sure the key is correct by checking spdx_id
                spdx_id_in_cache = cachedEntry.get("spdx_id")
                if spdx_id_in_cache and spdx_id_in_cache.lower() != cached_spdx_lower:
                    print(f"  SPDX ID mismatch for cached {name}. Updating key.")
                    processedLicenses[spdx_id_in_cache.lower()] = cachedEntry
                    needsSave = True  # Need to save cache with corrected key
                else:
                    processedLicenses[cached_spdx_lower] = cachedEntry
            else:
                # New file or changed file, mark for fetching content
                if cachedEntry:
                    print(f"  Detected change in {name} (SHA mismatch).")
                else:
                    print(f"  Detected new file {name}.")
                licenseFilesToFetch.append(ghInfo)
                needsSave = True  # Cache will be updated

        # Identify deleted license files
        cachedLicenseFilenames = {
            entry.get("filename")
            for key, entry in cachedData.items()
            if not key.startswith("data:")
            and isinstance(entry, dict)
            and entry.get("filename")
        }
        deletedLicenseFilenames = cachedLicenseFilenames - set(
            currentGithubLicenseFiles.keys()
        )
        if deletedLicenseFilenames:
            print(
                f"  Detected deleted license files: {', '.join(deletedLicenseFilenames)}"
            )
            # No need to explicitly remove, they just won't be added to processedLicenses
            needsSave = True

    # Fetch content for new/changed license files
    if licenseFilesToFetch:
        print(
            f"Fetching content for {len(licenseFilesToFetch)} new/updated license files..."
        )
        for ghInfo in licenseFilesToFetch:
            print(f"  Fetching {ghInfo['name']}...")
            content = FetchFileContent(ghInfo["download_url"])
            if content:
                parsedData = ParseLicenseFile(ghInfo["name"], content)
                if parsedData:
                    spdx_lower = parsedData["spdx_id"].lower()
                    # Store basic info from front matter + filename + sha + full content
                    fm = parsedData["front_matter"]
                    processedLicenses[spdx_lower] = {
                        "spdx_id": parsedData["spdx_id"],
                        "title": fm.get("title", parsedData["spdx_id"]),
                        "filename": ghInfo["name"],
                        "sha": ghInfo.get("sha", ""),
                        "nickname": fm.get("nickname"),  # Store for detailed list
                        "description": fm.get("description"),  # Store for detailed list
                        "permissions": fm.get(
                            "permissions", []
                        ),  # Store for detailed list
                        "conditions": fm.get(
                            "conditions", []
                        ),  # Store for detailed list
                        "limitations": fm.get(
                            "limitations", []
                        ),  # Store for detailed list
                        "file_content_cached": content,  # Cache the full file content
                    }
                else:
                    print(f"  Failed to parse license file {ghInfo['name']}. Skipping.")
            else:
                print(f"  Failed to fetch content for {ghInfo['name']}.")

    # Combine updated data file cache with updated license cache
    finalCacheData = {**processedDataFiles, **processedLicenses}

    if needsSave:
        print("Saving updated cache...")
        SaveCache(cacheFilePath, finalCacheData)
    else:
        print("Cache is up-to-date.")

    return finalCacheData


# --- Display and Filling Functions ---


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


def ListLicenses(licensesData: dict[str, object]) -> None:
    """Prints a simple list of available licenses from cached data."""
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        print("No licenses found in cache.")
        return
    print("\nAvailable Licenses (SPDX ID: Title):")
    print("-" * 50)
    sortedKeys: list[str] = sorted(licenseKeys)
    for spdxLower in sortedKeys:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        print(f"  {spdx:<25} : {title}")
    print("-" * 50)


def PrintDetailedList(licensesData: dict[str, object]) -> None:
    """Prints a detailed list of licenses using cached basic info."""
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        print("No licenses found in cache.")
        return

    print("\n--- Detailed License List (from cache) ---")
    sortedKeys: list[str] = sorted(licenseKeys)
    for spdxLower in sortedKeys:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        nickname: str = data.get("nickname", "N/A")
        description: str = data.get("description", "No description available.")
        perms_count = len(data.get("permissions", []))
        conds_count = len(data.get("conditions", []))
        lims_count = len(data.get("limitations", []))

        print(f"\nSPDX ID: {spdx}")
        print(f"Title: {title}")
        if nickname and nickname != "N/A":
            print(f"Nickname: {nickname}")
        # Truncate description for list view
        truncated_desc = textwrap.shorten(description, width=70, placeholder="...")
        print(f"Description: {truncated_desc}")
        print(
            f"Rules: Permissions ({perms_count}), Conditions ({conds_count}), Limitations ({lims_count})"
        )
        print("---")


def GetFullLicenseData(
    spdxIdLower: str, licensesData: dict[str, object]
) -> dict[str, object] | None:
    """Retrieves full license data, fetching from GitHub if not fully cached."""
    basicInfo = licensesData.get(spdxIdLower)
    if not basicInfo:
        print(f"Error: Basic info for {spdxIdLower.upper()} not found in cache.")
        return None

    content = basicInfo.get("file_content_cached")
    fullLicenseData = None

    if content:
        # print(f"Using cached content for {basicInfo.get('filename', spdxIdLower.upper())}.")
        fullLicenseData = ParseLicenseFile(
            basicInfo.get("filename", "unknown"), content
        )
        if not fullLicenseData:
            print(
                f"Warning: Failed to parse cached content for {basicInfo.get('filename', spdxIdLower.upper())}. Re-fetching."
            )
            content = None  # Force re-fetch

    if not content:  # Need to fetch
        filename = basicInfo.get("filename")
        if not filename:
            print(
                f"Error: Filename missing for {spdxIdLower.upper()} in cache. Cannot fetch full info."
            )
            return None

        print(f"Fetching full content for {filename} from GitHub...")
        # Need download URL - fetch list briefly or store it? Assume cache is recent enough for now.
        # Let's fetch the list again if needed - suboptimal.
        # TODO: Add download_url to cached basic info in UpdateAndLoadLicenseCache
        githubFiles = FetchGithubDirListing(LICENSES_PATH)
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            print(
                f"Error: Could not find download URL for {filename}. Cannot fetch content."
            )
            return None

        content = FetchFileContent(downloadUrl)
        if content:
            fullLicenseData = ParseLicenseFile(filename, content)
            # Optionally update the cache with this fetched content here?
            # licensesData[spdxIdLower]['file_content_cached'] = content
            # licensesData[spdxIdLower]['sha'] = new_sha # Need SHA from listing
        else:
            print(f"Error: Failed to fetch content for {filename}.")
            return None

    if not fullLicenseData:
        print(f"Error: Failed to get full license data for {spdxIdLower.upper()}.")
        return None

    return fullLicenseData


def DisplayLicenseInfo(spdxIdLower: str, licensesData: dict[str, object]) -> None:
    """Prints the formatted metadata for a license using GetFullLicenseData."""
    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)
    if not fullLicenseData:
        return  # Error message already printed

    fm: dict[str, object] = fullLicenseData.get("front_matter", {})
    spdxId: str = fullLicenseData.get("spdx_id", "N/A")
    title: str = fm.get("title", "N/A")
    body = fullLicenseData.get("body", "")

    # Load rules and fields data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"]: item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }  # Convert list to dict

    print(f"\n--- License Information: {title} ({spdxId}) ---")

    if fm.get("nickname"):
        print(f"\nNickname: {fm['nickname']}")

    def PrintTextBlock(label: str, text: str | None) -> None:
        if text:
            print(f"\n{label}:")
            print(
                textwrap.fill(
                    text, width=78, initial_indent="  ", subsequent_indent="  "
                )
            )

    PrintTextBlock("Description", fm.get("description"))
    PrintTextBlock("How to Apply", fm.get("how"))

    def PrintRulesWithLabels(label: str, key: str, rulesConfig: dict) -> None:
        ruleTags = fm.get(key, [])
        configRules = rulesConfig.get(key, [])
        configRulesMap = {
            rule.get("tag"): rule
            for rule in configRules
            if isinstance(rule, dict) and rule.get("tag")
        }
        if ruleTags and isinstance(ruleTags, list):
            print(f"\n{label}:")
            for tag in ruleTags:
                ruleInfo = configRulesMap.get(tag)
                if ruleInfo and ruleInfo.get("label"):
                    print(f"  - {ruleInfo['label']}")
                else:
                    print(f"  - {tag} (Label not found in rules.yml)")
        # else: print(f"\n{label}: (None specified)") # Option to show empty sections

    PrintRulesWithLabels("Permissions", "permissions", rulesData)
    PrintRulesWithLabels("Conditions", "conditions", rulesData)
    PrintRulesWithLabels("Limitations", "limitations", rulesData)

    if fm.get("using") and isinstance(fm["using"], dict):
        print("\nNotable Projects Using This License:")
        for project, url in fm["using"].items():
            print(f"  - {project}: {url}")

    PrintTextBlock("Note", fm.get("note"))

    placeholders = FindPlaceholders(body)
    if placeholders:
        print("\nPlaceholders in Body:")
        for ph in sorted(list(placeholders)):
            ph_lower = ph.lower()
            fieldInfo = fieldsData.get(ph_lower)
            description = (
                fieldInfo.get("description", "No description available")
                if fieldInfo
                else "No description available"
            )
            argSuggestion = PLACEHOLDER_TO_ARG_MAP.get(
                ph_lower, f"(no direct argument for '[{ph}]')"
            )
            defaultInfo = ""
            if ph_lower in ["year", "yyyy"]:
                defaultInfo = " (defaults to current year)"
            print(f"  - [{ph}]")
            print(f"    Description: {description}")
            print(f"    Argument: {argSuggestion}{defaultInfo}")
    else:
        print("\nPlaceholders in Body: (None detected)")

    print("\n--- End License Information ---")


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """Compares licenses based on rules."""
    if len(spdxIdsLower) < 2:
        print("\nError: Need at least two license SPDX IDs to compare.")
        return

    licensesToCompare = []
    for spdxLower in spdxIdsLower:
        fullLicenseData = GetFullLicenseData(spdxLower, licensesData)
        if fullLicenseData:
            licensesToCompare.append(fullLicenseData)
        else:
            print(f"Could not get data for {spdxLower.upper()}, skipping.")

    if len(licensesToCompare) < 2:
        print("\nCannot perform comparison: Need at least two valid licenses.")
        return

    # Load rules data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    rulesMap = {}
    for category in ["permissions", "conditions", "limitations"]:
        rulesMap[category] = {
            rule.get("tag"): rule
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        }

    print("\n--- Comparing Licenses ---")
    licenseSpdxIds = [lic["spdx_id"] for lic in licensesToCompare]
    print("Comparing:", ", ".join(licenseSpdxIds))

    # Collect all unique rule tags across all licenses being compared
    allRuleTags = set()
    for lic in licensesToCompare:
        fm = lic.get("front_matter", {})
        for cat in ["permissions", "conditions", "limitations"]:
            allRuleTags.update(
                fm.get(cat, [])
            )  # Ensure update works on potentially missing keys

    # Separate tags by category using rulesMap
    tagsByCategory = {
        "permissions": sorted(
            [tag for tag in allRuleTags if tag in rulesMap.get("permissions", {})]
        ),
        "conditions": sorted(
            [tag for tag in allRuleTags if tag in rulesMap.get("conditions", {})]
        ),
        "limitations": sorted(
            [tag for tag in allRuleTags if tag in rulesMap.get("limitations", {})]
        ),
    }

    # Determine max label width for alignment
    maxLabelWidth = 0
    for category_tags in tagsByCategory.values():
        for tag in category_tags:
            ruleInfo = None
            for cat in rulesMap:  # Check all categories for the tag
                if tag in rulesMap[cat]:
                    ruleInfo = rulesMap[cat][tag]
                    break
            if ruleInfo and ruleInfo.get("label"):
                maxLabelWidth = max(maxLabelWidth, len(ruleInfo["label"]))
        maxLabelWidth = max(maxLabelWidth, 25)  # Ensure minimum width

    # Print comparison by rule category
    for category in ["permissions", "conditions", "limitations"]:
        ruleTags = tagsByCategory[category]
        if not ruleTags:
            continue  # Skip category if no relevant rules

        print(f"\n{category.capitalize()}:")
        for tag in ruleTags:
            ruleInfo = rulesMap.get(category, {}).get(tag)
            label = (
                ruleInfo.get("label", tag) if ruleInfo else tag
            )  # Use tag as fallback label

            line = f"  {label:<{maxLabelWidth}} : "
            indicators = []
            for lic in licensesToCompare:
                fm = lic.get("front_matter", {})
                hasRule = tag in fm.get(category, [])
                indicators.append(f"{lic['spdx_id']}: {'✓' if hasRule else 'X'}")
            line += " | ".join(indicators)
            print(line)

    print("\n--- End Comparison ---")


# --- Main Execution ---


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, display info for, compare, or fill open source license templates from github/choosealicense.com using local caching.",
        formatter_class=argparse.RawTextHelpFormatter,  # Use RawText to better control epilog formatting
        epilog=textwrap.dedent(
            """\
Examples:
  %(prog)s --list                           List all available licenses (uses cache).
  %(prog)s --detailed-list                  List licenses with key details (uses cache).
  %(prog)s --refresh --list                 Force refresh cache then list licenses.
  %(prog)s --info MIT                       Show detailed info (fetches full content if needed).
  %(prog)s --show-placeholders NCSA         Show placeholders (fetches full content if needed).
  %(prog)s --compare MIT Apache-2.0 GPL-3.0 Compare licenses (fetches full content if needed).
  %(prog)s --license MIT -f "Jane Doe"      Fill license (fetches full content if needed).
  %(prog)s -l Apache-2.0 -f ACME -o LIC     Fill license, output to file LIC (fetches full content if needed).
"""
        ),
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh of the local license and data cache from GitHub.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,  # Use Path for type hint
        default=Path(CACHE_FILENAME),  # Use Path for default
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
        "--detailed-list",
        action="store_true",
        help="List licenses with key details from cache and exit.",
    )
    actionGroup.add_argument(
        "--info",
        metavar="LICENSE_ID",
        help="Show detailed metadata (fetches full content if needed).",
    )
    actionGroup.add_argument(
        "--show-placeholders",
        metavar="LICENSE_ID",
        help="Show placeholders (fetches full content if needed).",
    )
    actionGroup.add_argument(
        "--compare",
        nargs="+",
        metavar="LICENSE_ID",
        help="Compare the specified licenses based on rules and metadata.",
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
    cacheFilePath = args.cache_file  # Already a Path object

    # Update cache if needed, then load basic license info AND data files
    licensesData = UpdateAndLoadLicenseCache(cacheFilePath, args.refresh)
    if not licensesData:
        print("\nOperation failed: Could not load or update license data.")
        return 1

    # --- Handle Actions ---

    if args.list:
        ListLicenses(licensesData)
        return 0

    if args.detailed_list:
        PrintDetailedList(licensesData)
        return 0

    if args.info:
        requestedIdLower: str = args.info.lower()
        if not licensesData.get(requestedIdLower):  # Check cache first
            print(f"\nError: License '{args.info}' not found in cache.")
            print("Use --list to see available licenses or --refresh to update cache.")
            return 1
        DisplayLicenseInfo(
            requestedIdLower, licensesData
        )  # Fetches full content inside if needed
        return 0

    if args.show_placeholders:
        requestedIdLower = args.show_placeholders.lower()
        fullLicenseData = GetFullLicenseData(
            requestedIdLower, licensesData
        )  # Needs full body
        if not fullLicenseData:
            return 1  # Error message already printed

        basicInfo = licensesData.get(
            requestedIdLower, {}
        )  # Get basic info for title/spdx display
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        fieldsData = {
            item["name"]: item
            for item in fieldsDataList
            if isinstance(item, dict) and item.get("name")
        }

        print(
            f"\nPlaceholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}):"
        )
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
        if not placeholders:
            print("  (No standard [placeholder] patterns found)")
        else:
            for ph in sorted(list(placeholders)):
                ph_lower = ph.lower()
                fieldInfo = fieldsData.get(ph_lower)
                description = (
                    fieldInfo.get("description", "No description available")
                    if fieldInfo
                    else "No description available"
                )
                argSuggestion = PLACEHOLDER_TO_ARG_MAP.get(
                    ph_lower, f"(no direct argument for '[{ph}]')"
                )
                defaultInfo = ""
                if ph_lower in ["year", "yyyy"]:
                    defaultInfo = " (defaults to current year if not provided)"
                print(f"  - [{ph}]")
                print(f"    Description: {description}")
                print(f"    Argument: {argSuggestion}{defaultInfo}")
        return 0

    if args.compare:
        spdxIdsToCompare = [id.lower() for id in args.compare]
        # Check if all requested licenses are in the cache first (basic info)
        allFoundInCache = True
        for spdxLower in spdxIdsToCompare:
            if spdxLower not in licensesData:
                print(
                    f"\nError: License '{spdxLower.upper()}' not found in cache. Cannot compare."
                )
                print(
                    "Use --list to see available licenses or --refresh to update cache."
                )
                allFoundInCache = False
                break
        if not allFoundInCache:
            return 1

        CompareLicenses(
            spdxIdsToCompare, licensesData
        )  # Fetches full content inside if needed
        return 0

    if args.license:
        requestedLicenseIdLower: str = args.license.lower()
        fullLicenseData = GetFullLicenseData(
            requestedLicenseIdLower, licensesData
        )  # Needs full body
        if not fullLicenseData:
            return 1  # Error message already printed

        title: str = fullLicenseData.get("front_matter", {}).get("title", "N/A")
        spdxId: str = fullLicenseData.get("spdx_id", "N/A")
        body: str = fullLicenseData.get("body", "")

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

        # --- Check Placeholders ---
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        fieldsData = {
            item["name"]: item
            for item in fieldsDataList
            if isinstance(item, dict) and item.get("name")
        }

        foundPlaceholders = FindPlaceholders(body)
        missingArgs: bool = False
        print("Checking required placeholders:")
        for ph in foundPlaceholders:
            ph_lower = ph.lower()
            phCheckKey: str = ph_lower
            if phCheckKey == "yyyy":
                phCheckKey = "year"
            if phCheckKey == "name of copyright owner":
                phCheckKey = "fullname"

            if phCheckKey not in replacements:
                argSuggestion: str = PLACEHOLDER_TO_ARG_MAP.get(ph_lower, f"'[{ph}]'")
                fieldInfo = fieldsData.get(ph_lower)
                description = (
                    fieldInfo.get("description", "No description available")
                    if fieldInfo
                    else "No description available"
                )
                print(
                    f"  Warning: Placeholder [{ph}] ({description}) found, but no value provided via {argSuggestion}."
                )
                missingArgs = True
        if missingArgs:
            print("  License generated, but placeholders might remain unfilled.")

        # --- Fill and Output ---
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
        "\nError: No action specified. Use --list, --detailed-list, --info, --show-placeholders, --compare, or --license."
    )
    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
