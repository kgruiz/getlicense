import argparse
import base64
import json  # For caching
import os
import re
import sys  # Needed for stderr
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

# --- Verbose Print Helper ---
# Global flag to store verbose state, set in main()
_VERBOSE = False


def verbose_print(*args, **kwargs):
    """Prints only if the verbose flag is set."""
    if _VERBOSE:
        # Print to stderr to separate status messages from potential stdout license text
        print(*args, file=sys.stderr, **kwargs)


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
        # Essential Error
        print(f"Error: Timeout while fetching from GitHub API ({url})", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        # Essential Error
        print(f"Error fetching from GitHub API ({url}): {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"Response Status: {e.response.status_code}", file=sys.stderr)
            print(f"Response Body: {e.response.text[:500]}...", file=sys.stderr)
            if e.response.status_code == 403:
                rate_limit_info = e.response.headers.get("X-RateLimit-Remaining", "N/A")
                print(
                    f"Hint: Check GitHub API rate limits (Remaining: {rate_limit_info}) or authentication (set GITHUB_TOKEN).",
                    file=sys.stderr,
                )
        return None
    except Exception as e:
        # Essential Error
        print(f"An unexpected error occurred during API call: {e}", file=sys.stderr)
        return None


def FetchGithubDirListing(repo_path: str) -> list[dict[str, object]] | None:
    """Fetches the list of files in a directory from the GitHub repo API."""
    verbose_print(f"Fetching current file list from GitHub ({repo_path})...")
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{repo_path}?ref={BRANCH}"
    data = GetGithubApi(endpoint)
    if not data or not isinstance(data, list):
        # Essential Error (unless cache exists)
        print(
            f"Error: Could not fetch or parse directory listing for {repo_path}",
            file=sys.stderr,
        )
        return None
    return data


def FetchFileContent(downloadUrl: str) -> str | None:
    """Fetches the content of a single file from a direct download URL."""
    # This fetch is usually triggered for a specific file or updates,
    # so the calling function should handle verbose printing if needed.
    try:
        response = requests.get(downloadUrl, timeout=10)  # Add timeout
        response.raise_for_status()
        return response.text
    except requests.exceptions.Timeout:
        # Essential Error for the specific file fetch
        print(f"Error: Timeout fetching content from {downloadUrl}", file=sys.stderr)
        return None
    except requests.exceptions.RequestException as e:
        # Essential Error for the specific file fetch
        print(f"Error fetching content from {downloadUrl}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        # Essential Error for the specific file fetch
        print(f"An unexpected error occurred fetching content: {e}", file=sys.stderr)
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
                    verbose_print(
                        f"Warning: Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}
                # Get SPDX ID from parsed data first
                spdxId = frontMatter.get("spdx-id")

            except yaml.YAMLError as e:
                verbose_print(
                    f"Warning: YAML parse error for {filename}: {e}. Fallback."
                )
                frontMatter = {}
                # Fallback regex search if YAML fails
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()

        else:
            verbose_print(f"Warning: Malformed front matter in {filename}.")
            # Fallback: Guess SPDX ID from filename if needed
            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)
    else:
        verbose_print(f"Warning: No front matter '---' in {filename}.")
        spdxId = GuessSpdxFromFilename(filename)

    # Final check and fallback for SPDX ID
    if not spdxId and "spdx-id" in frontMatter:
        spdxId = frontMatter["spdx-id"]
    if not spdxId:
        # Essential Error for this file
        print(
            f"Error: Could not determine SPDX ID for {filename}. Skipping.",
            file=sys.stderr,
        )
        return None

    # Ensure basic fields exist in frontMatter for consistency in cache structure
    frontMatter.setdefault("spdx-id", spdxId)
    frontMatter.setdefault("title", spdxId)  # Fallback title
    frontMatter.setdefault("nickname", None)
    frontMatter.setdefault("description", None)
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
        verbose_print(
            f"Warning: Filename {filename} doesn't look like a typical SPDX ID format. Cannot reliably guess."
        )
        return None


def ParseDataFile(filename: str, fileContent: str) -> object | None:
    """Parses YAML data file content."""
    try:
        data = yaml.safe_load(fileContent)
        return data
    except yaml.YAMLError as e:
        # Treat as essential if data files are critical, verbose otherwise
        print(f"Error parsing YAML data file {filename}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(
            f"An unexpected error occurred parsing data file {filename}: {e}",
            file=sys.stderr,
        )
        return None


# --- Caching Functions ---


def LoadCache(cacheFilePath: Path) -> dict[str, object]:
    """Loads the license cache from a JSON file."""
    if cacheFilePath.exists():
        try:
            with open(cacheFilePath, "r", encoding="utf-8") as f:
                content = f.read()
                if not content:
                    verbose_print(
                        f"Warning: Cache file {cacheFilePath} is empty. Starting fresh."
                    )
                    return {}
                return json.loads(content)
        except (IOError, json.JSONDecodeError) as e:
            verbose_print(
                f"Warning: Could not load or parse cache file {cacheFilePath}: {e}. Starting fresh."
            )
            return {}  # Return empty cache on error
        except Exception as e:
            verbose_print(f"An unexpected error occurred loading cache: {e}")
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
        verbose_print(f"Cache saved to {cacheFilePath}")
    except IOError as e:
        # Essential Error
        print(f"Error: Could not save cache file {cacheFilePath}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"An unexpected error occurred saving cache: {e}", file=sys.stderr)


def UpdateAndLoadLicenseCache(
    cacheFilePath: Path, forceRefresh: bool = False
) -> dict[str, object]:
    """
    Loads cache, checks GitHub for updates in licenses and data files,
    fetches necessary files, updates cache, saves cache, and returns the
    up-to-date cache data (basic license info and data files).
    """
    verbose_print("Loading cache...")
    cachedData = LoadCache(cacheFilePath) if not forceRefresh else {}
    if forceRefresh:
        verbose_print("Cache refresh forced.")

    needsSave = False
    processedDataFiles = {}  # Store processed data files separately

    # --- Update Data Files (_data/*.yml) ---
    dataFilesToFetch = []
    # FetchGithubDirListing prints verbose message inside
    githubDataFiles = FetchGithubDirListing(DATA_PATH)
    if githubDataFiles is None:
        verbose_print(
            "Warning: Failed to fetch data file list. Using potentially stale cached data."
        )
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
                verbose_print(
                    f"Warning: No SHA for data file {name}. Assuming changed."
                )
                dataFilesToFetch.append(ghInfo)
                needsSave = True
                continue

            if (
                cachedEntry
                and isinstance(cachedEntry, dict)
                and cachedEntry.get("sha") == gh_sha
            ):
                processedDataFiles[cacheKey] = cachedEntry
            else:
                if cachedEntry:
                    verbose_print(
                        f"  Detected change in data file {name} (SHA mismatch)."
                    )
                else:
                    verbose_print(f"  Detected new data file {name}.")
                dataFilesToFetch.append(ghInfo)
                needsSave = True

        cachedDataFilenames = {
            k.split(":", 1)[1] for k in cachedData.keys() if k.startswith("data:")
        }
        deletedDataFilenames = cachedDataFilenames - set(currentGithubDataFiles.keys())
        if deletedDataFilenames:
            verbose_print(
                f"  Detected deleted data files: {', '.join(deletedDataFilenames)}"
            )
            needsSave = True

    if dataFilesToFetch:
        verbose_print(
            f"Fetching content for {len(dataFilesToFetch)} new/updated data files..."
        )
        for ghInfo in dataFilesToFetch:
            verbose_print(f"  Fetching {ghInfo['name']}...")
            content = FetchFileContent(ghInfo["download_url"])
            if content:
                parsedData = ParseDataFile(ghInfo["name"], content)
                if parsedData is not None:
                    processedDataFiles[f'data:{ghInfo["name"]}'] = {
                        "sha": ghInfo.get("sha", ""),
                        "content": parsedData,
                    }
                elif f'data:{ghInfo["name"]}' in cachedData:
                    verbose_print(
                        f"  Failed to parse {ghInfo['name']}, keeping old cached version if it exists."
                    )
                    processedDataFiles[f'data:{ghInfo["name"]}'] = cachedData[
                        f'data:{ghInfo["name"]}'
                    ]
            else:
                verbose_print(f"  Failed to fetch data file {ghInfo['name']}.")

    # --- Update License Files (_licenses/*.txt) ---
    licenseFilesToFetch = []
    # FetchGithubDirListing prints verbose message inside
    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)
    processedLicenses = {}

    if githubLicenseFiles is None:
        verbose_print(
            "Warning: Failed to fetch license file list. Using potentially stale cached licenses."
        )
        for key, val in cachedData.items():
            if not key.startswith("data:"):
                processedLicenses[key] = val
    else:
        currentGithubLicenseFiles = {
            item["name"]: item for item in githubLicenseFiles if isinstance(item, dict)
        }

        for name, ghInfo in currentGithubLicenseFiles.items():
            if not name.endswith(".txt"):
                continue

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
                verbose_print(
                    f"Warning: No SHA for license file {name}. Assuming changed."
                )
                licenseFilesToFetch.append(ghInfo)
                needsSave = True
                continue

            if cachedEntry and cachedEntry.get("sha") == gh_sha:
                # SHA matches, use cached basic info + content if available
                spdx_id_in_cache = cachedEntry.get("spdx_id")
                if spdx_id_in_cache and spdx_id_in_cache.lower() != cached_spdx_lower:
                    verbose_print(
                        f"  SPDX ID mismatch for cached {name}. Updating key."
                    )
                    processedLicenses[spdx_id_in_cache.lower()] = cachedEntry
                    needsSave = True
                else:
                    processedLicenses[cached_spdx_lower] = cachedEntry
            else:
                # New file or changed file, mark for fetching content
                if cachedEntry:
                    verbose_print(f"  Detected change in {name} (SHA mismatch).")
                else:
                    verbose_print(f"  Detected new file {name}.")
                licenseFilesToFetch.append(ghInfo)
                needsSave = True

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
            verbose_print(
                f"  Detected deleted license files: {', '.join(deletedLicenseFilenames)}"
            )
            needsSave = True

    if licenseFilesToFetch:
        verbose_print(
            f"Fetching content for {len(licenseFilesToFetch)} new/updated license files..."
        )
        for ghInfo in licenseFilesToFetch:
            verbose_print(f"  Fetching {ghInfo['name']}...")
            content = FetchFileContent(ghInfo["download_url"])
            if content:
                parsedData = ParseLicenseFile(ghInfo["name"], content)
                if parsedData:
                    spdx_lower = parsedData["spdx_id"].lower()
                    fm = parsedData["front_matter"]
                    processedLicenses[spdx_lower] = {
                        "spdx_id": parsedData["spdx_id"],
                        "title": fm.get("title", parsedData["spdx_id"]),
                        "filename": ghInfo["name"],
                        "sha": ghInfo.get("sha", ""),
                        "nickname": fm.get("nickname"),
                        "description": fm.get("description"),
                        "permissions": fm.get("permissions", []),
                        "conditions": fm.get("conditions", []),
                        "limitations": fm.get("limitations", []),
                        "file_content_cached": content,  # Cache the full file content
                    }
                elif ghInfo["name"] in {
                    v.get("filename")
                    for k, v in cachedData.items()
                    if not k.startswith("data:")
                }:
                    verbose_print(
                        f"  Failed to parse {ghInfo['name']}, keeping old cached version if it exists."
                    )
                    for key, val in cachedData.items():
                        if (
                            not key.startswith("data:")
                            and isinstance(val, dict)
                            and val.get("filename") == ghInfo["name"]
                        ):
                            processedLicenses[key] = val
                            break
            else:
                verbose_print(f"  Failed to fetch content for {ghInfo['name']}.")

    all_processed_filenames = {
        v.get("filename") for v in processedLicenses.values() if v.get("filename")
    }
    keys_to_remove_from_processed = [
        key
        for key, val in processedLicenses.items()
        if not key.startswith("data:")
        and val.get("filename") in deletedLicenseFilenames
    ]
    for key in keys_to_remove_from_processed:
        processedLicenses.pop(key, None)

    finalCacheData = {**processedDataFiles, **processedLicenses}  # Combine

    if needsSave or deletedDataFilenames or deletedLicenseFilenames:
        # verbose_print("Saving updated cache...") # SaveCache handles verbose print
        SaveCache(cacheFilePath, finalCacheData)
    else:
        verbose_print("Cache is up-to-date.")

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
        print("No licenses found in cache.")  # Essential info
        return
    print("\nAvailable Licenses (SPDX ID: Title):")  # Essential output
    print("-" * 50)
    sortedKeys: list[str] = sorted(licenseKeys)
    for spdxLower in sortedKeys:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        print(f"  {spdx:<25} : {title}")  # Essential output
    print("-" * 50)


def PrintDetailedList(licensesData: dict[str, object]) -> None:
    """Prints a detailed list of licenses using cached basic info and rule labels."""
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        print("No licenses found in cache.")  # Essential info
        return

    # Load rules data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        verbose_print(
            "Warning: rules.yml data not found in cache. Rule labels may be missing."
        )
        rulesMap = {}
    else:
        rulesMap = {}
        for category in ["permissions", "conditions", "limitations"]:
            rulesMap[category] = {
                rule.get("tag"): rule
                for rule in rulesData.get(category, [])
                if isinstance(rule, dict) and rule.get("tag")
            }

    print("\n--- Detailed License List (from cache) ---")  # Essential output
    sortedKeys: list[str] = sorted(licenseKeys)
    for i, spdxLower in enumerate(sortedKeys):
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        nickname: str | None = data.get("nickname")  # Use .get for optional fields
        description: str = data.get("description", "No description available.")
        perms_tags = data.get("permissions", [])
        conds_tags = data.get("conditions", [])
        lims_tags = data.get("limitations", [])

        print(f"\nSPDX ID: {spdx}")  # Essential output
        print(f"Title: {title}")  # Essential output
        if nickname:
            print(f"Nickname: {nickname}")  # Essential output

        # Truncate description for list view
        truncated_desc = textwrap.shorten(
            description or "", width=100, placeholder="..."
        )
        print(f"Description: {truncated_desc}")  # Essential output

        # List rule labels
        def GetRuleLabels(tags: list[str], category: str) -> list[str]:
            labels = []
            catRulesMap = rulesMap.get(category, {})
            for tag in tags:
                ruleInfo = catRulesMap.get(tag)
                labels.append(
                    ruleInfo.get("label", tag) if ruleInfo else tag
                )  # Use tag as fallback
            return sorted(labels)  # Sort labels alphabetically

        perm_labels = GetRuleLabels(perms_tags, "permissions")
        cond_labels = GetRuleLabels(conds_tags, "conditions")
        lim_labels = GetRuleLabels(lims_tags, "limitations")

        print(
            f"Permissions ({len(perm_labels)}): {', '.join(perm_labels) if perm_labels else 'None'}"
        )  # Essential output
        print(
            f"Conditions ({len(cond_labels)}): {', '.join(cond_labels) if cond_labels else 'None'}"
        )  # Essential output
        print(
            f"Limitations ({len(lim_labels)}): {', '.join(lim_labels) if lim_labels else 'None'}"
        )  # Essential output

        if i < len(sortedKeys) - 1:  # Print separator except for the last one
            print("---")

    print("\n--- End Detailed License List ---")  # Essential output


def GetFullLicenseData(
    spdxIdLower: str, licensesData: dict[str, object]
) -> dict[str, object] | None:
    """Retrieves full license data, fetching from GitHub if not fully cached."""
    basicInfo = licensesData.get(spdxIdLower)
    if not basicInfo:
        # Essential Error
        print(
            f"Error: Basic info for {spdxIdLower.upper()} not found in cache.",
            file=sys.stderr,
        )
        return None

    content = basicInfo.get("file_content_cached")
    fullLicenseData = None

    if content:
        verbose_print(
            f"Using cached content for {basicInfo.get('filename', spdxIdLower.upper())}."
        )
        fullLicenseData = ParseLicenseFile(
            basicInfo.get("filename", "unknown"), content
        )
        if not fullLicenseData:
            verbose_print(
                f"Warning: Failed to parse cached content for {basicInfo.get('filename', spdxIdLower.upper())}. Re-fetching."
            )
            content = None  # Force re-fetch

    if not content:  # Need to fetch
        filename = basicInfo.get("filename")
        if not filename:
            # Essential Error
            print(
                f"Error: Filename missing for {spdxIdLower.upper()} in cache. Cannot fetch full info.",
                file=sys.stderr,
            )
            return None

        verbose_print(f"Fetching full content for {filename} from GitHub...")
        githubFiles = FetchGithubDirListing(LICENSES_PATH)
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if isinstance(item, dict) and item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            # Essential Error
            print(
                f"Error: Could not find download URL for {filename}. Cannot fetch content.",
                file=sys.stderr,
            )
            return None

        content = FetchFileContent(downloadUrl)
        if content:
            fullLicenseData = ParseLicenseFile(filename, content)
        else:
            # FetchFileContent prints essential error
            return None

    if not fullLicenseData:
        # ParseLicenseFile prints essential error
        print(
            f"Error: Failed to get full license data for {spdxIdLower.upper()}.",
            file=sys.stderr,
        )
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
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }  # Use lower key

    print(f"\n--- License Information: {title} ({spdxId}) ---")  # Essential Output

    if fm.get("nickname"):
        print(f"\nNickname: {fm['nickname']}")  # Essential Output

    def PrintTextBlock(label: str, text: str | None) -> None:
        if text:
            print(f"\n{label}:")  # Essential Output
            print(
                textwrap.fill(
                    text, width=78, initial_indent="  ", subsequent_indent="  "
                )
            )  # Essential Output

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
            print(f"\n{label}:")  # Essential Output
            sorted_tags = sorted(ruleTags)  # Sort tags for consistent order
            for tag in sorted_tags:
                ruleInfo = configRulesMap.get(tag)
                if ruleInfo and ruleInfo.get("label"):
                    print(f"  - {ruleInfo['label']} ({tag})")  # Essential Output
                else:
                    print(
                        f"  - {tag} (Label not found in rules.yml)"
                    )  # Essential Output

    PrintRulesWithLabels("Permissions", "permissions", rulesData)
    PrintRulesWithLabels("Conditions", "conditions", rulesData)
    PrintRulesWithLabels("Limitations", "limitations", rulesData)

    if fm.get("using") and isinstance(fm["using"], dict):
        print("\nNotable Projects Using This License:")  # Essential Output
        for project, url in fm["using"].items():
            print(f"  - {project}: {url}")  # Essential Output

    PrintTextBlock("Note", fm.get("note"))

    placeholders = FindPlaceholders(body)
    if placeholders:
        print("\nPlaceholders in Body:")  # Essential Output
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
            print(f"  - [{ph}]")  # Essential Output
            print(f"    Description: {description}")  # Essential Output
            print(f"    Argument: {argSuggestion}{defaultInfo}")  # Essential Output
    else:
        print("\nPlaceholders in Body: (None detected)")  # Essential Output

    print("\n--- End License Information ---")  # Essential Output


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """Compares licenses based on rules."""
    if len(spdxIdsLower) < 2:
        # Essential Error
        print(
            "\nError: Need at least two license SPDX IDs to compare.", file=sys.stderr
        )
        return

    licensesToCompare = []
    for spdxLower in spdxIdsLower:
        fullLicenseData = GetFullLicenseData(spdxLower, licensesData)
        if fullLicenseData:
            licensesToCompare.append(fullLicenseData)
        else:
            # GetFullLicenseData prints essential error
            print(
                f"Could not get data for {spdxLower.upper()}, skipping.",
                file=sys.stderr,
            )

    if len(licensesToCompare) < 2:
        # Essential Error
        print(
            "\nCannot perform comparison: Need at least two valid licenses.",
            file=sys.stderr,
        )
        return

    # Load rules data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        # Essential Error
        print(
            "Error: rules.yml data not found in cache. Cannot compare rules.",
            file=sys.stderr,
        )
        return

    rulesMap = {}
    for category in ["permissions", "conditions", "limitations"]:
        rulesMap[category] = {
            rule.get("tag"): rule
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        }

    print("\n--- Comparing Licenses ---")  # Essential Output
    licenseSpdxIds = [lic["spdx_id"] for lic in licensesToCompare]
    print("Comparing:", ", ".join(licenseSpdxIds))  # Essential Output

    # Collect all unique rule tags across all licenses being compared
    allRuleTags = set()
    for lic in licensesToCompare:
        fm = lic.get("front_matter", {})
        for cat in ["permissions", "conditions", "limitations"]:
            allRuleTags.update(fm.get(cat, []))

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
            for cat in rulesMap:
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
            continue

        print(f"\n{category.capitalize()}:")  # Essential Output
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
            print(line)  # Essential Output

    print("\n--- End Comparison ---")  # Essential Output


def FindLicenses(
    requireTags: list[str] | None,
    disallowTags: list[str] | None,
    licensesData: dict[str, object],
) -> None:
    """Finds licenses matching require/disallow criteria using cached data."""
    requireTags = requireTags or []
    disallowTags = disallowTags or []

    if not requireTags and not disallowTags:
        # Essential Error
        print(
            "\nError: Please provide at least one --require or --disallow tag for finding licenses.",
            file=sys.stderr,
        )
        return

    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        print("No licenses found in cache.")  # Essential Info
        return

    # Load rules data from cache to validate tags and find their categories
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        # Essential Error
        print(
            "Error: rules.yml data not found in cache. Cannot validate find tags.",
            file=sys.stderr,
        )
        return

    all_valid_tags = set()
    for category in ["permissions", "conditions", "limitations"]:
        all_valid_tags.update(
            rule.get("tag")
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        )

    # Validate input tags
    invalid_require = [tag for tag in requireTags if tag not in all_valid_tags]
    invalid_disallow = [tag for tag in disallowTags if tag not in all_valid_tags]

    if invalid_require or invalid_disallow:
        # Essential Error
        print("\nError: Invalid rule tags provided:", file=sys.stderr)
        if invalid_require:
            print(
                f"  Invalid --require tags: {', '.join(invalid_require)}",
                file=sys.stderr,
            )
        if invalid_disallow:
            print(
                f"  Invalid --disallow tags: {', '.join(invalid_disallow)}",
                file=sys.stderr,
            )
        return

    print("\n--- Finding Licenses Matching Criteria ---")  # Essential Output
    print(
        "Require:", ", ".join(requireTags) if requireTags else "None"
    )  # Essential Output
    print(
        "Disallow:", ", ".join(disallowTags) if disallowTags else "None"
    )  # Essential Output
    print("-" * 50)

    matches = []
    for spdxLower in licenseKeys:
        data = licensesData[spdxLower]
        # Use the rule lists stored in the cache
        license_rules = set(
            data.get("permissions", [])
            + data.get("conditions", [])
            + data.get("limitations", [])
        )

        # Check requirements
        meets_require = all(tag in license_rules for tag in requireTags)
        # Check disallowals
        meets_disallow = not object(tag in license_rules for tag in disallowTags)

        if meets_require and meets_disallow:
            matches.append(data)

    if not matches:
        print("No licenses found matching all criteria.")  # Essential Output
    else:
        print(f"Found {len(matches)} matching license(s):")  # Essential Output
        for match_data in sorted(matches, key=lambda x: x.get("spdx_id", "")):
            print(
                f"  - {match_data.get('spdx_id', 'N/A')} ({match_data.get('title', 'N/A')})"
            )  # Essential Output

    print("-" * 50)


# --- Main Execution ---


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, display info for, compare, find, or fill open source license templates from github/choosealicense.com using local caching.",
        formatter_class=argparse.RawTextHelpFormatter,  # Use RawText to better control epilog formatting
        epilog=textwrap.dedent(
            """\
Examples:
  %(prog)s --list                           List all available licenses (uses cache).
  %(prog)s --detailed-list                  List licenses with key details (uses cache).
  %(prog)s -v --refresh --list              Force refresh cache then list licenses (verbose).
  %(prog)s --info MIT                       Show detailed info (fetches full content if needed).
  %(prog)s --show-placeholders NCSA         Show placeholders (fetches full content if needed).
  %(prog)s --compare MIT Apache-2.0 GPL-3.0 Compare licenses (fetches full content if needed).
  %(prog)s --find --require commercial-use  Find licenses allowing commercial use.
  %(prog)s --find --require disclose-source --disallow liability Find licenses requiring source disclosure but without liability limitation.
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print detailed status messages during execution (to stderr).",
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
    actionGroup.add_argument(
        "--find",
        action="store_true",
        help="Find licenses matching specified criteria (use with --require/--disallow).",
    )

    findGroup = parser.add_argument_group(
        "Options for finding licenses (used with --find)"
    )
    findGroup.add_argument(
        "--require",
        nargs="+",
        metavar="RULE_TAG",
        default=[],
        help="List of rule tags that MUST be present.",
    )
    findGroup.add_argument(
        "--disallow",
        nargs="+",
        metavar="RULE_TAG",
        default=[],
        help="List of rule tags that MUST NOT be present.",
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

    # Set global verbose flag
    global _VERBOSE
    _VERBOSE = args.verbose

    # Update cache if needed, then load basic license info AND data files
    licensesData = UpdateAndLoadLicenseCache(cacheFilePath, args.refresh)
    if not licensesData:
        # Essential Error
        print(
            "\nOperation failed: Could not load or update license data.",
            file=sys.stderr,
        )
        return 1

    # --- Handle Actions ---

    if args.list:
        ListLicenses(licensesData)
        return 0

    if args.detailed_list:
        PrintDetailedList(licensesData)
        return 0

    if args.find:
        FindLicenses(args.require, args.disallow, licensesData)
        return 0

    if args.info:
        requestedIdLower: str = args.info.lower()
        if not licensesData.get(requestedIdLower):  # Check cache first
            # Essential Error
            print(
                f"\nError: License '{args.info}' not found in cache.", file=sys.stderr
            )
            print(
                "Use --list to see available licenses or --refresh to update cache.",
                file=sys.stderr,
            )
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
        # Handle case where fields data might not be cached yet
        if not fieldsDataList:
            print(
                "Warning: fields.yml data not found in cache. Placeholder descriptions unavailable.",
                file=sys.stderr,
            )
            fieldsData = {}
        else:
            fieldsData = {
                item["name"].lower(): item
                for item in fieldsDataList
                if isinstance(item, dict) and item.get("name")
            }

        print(
            f"\nPlaceholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}):"
        )  # Essential Output
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
        if not placeholders:
            print("  (No standard [placeholder] patterns found)")  # Essential Output
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
                print(f"  - [{ph}]")  # Essential Output
                print(f"    Description: {description}")  # Essential Output
                print(f"    Argument: {argSuggestion}{defaultInfo}")  # Essential Output
        return 0

    if args.compare:
        spdxIdsToCompare = [id.lower() for id in args.compare]
        # Check if all requested licenses are in the cache first (basic info)
        allFoundInCache = True
        for spdxLower in spdxIdsToCompare:
            if spdxLower not in licensesData:
                # Essential Error
                print(
                    f"\nError: License '{spdxLower.upper()}' not found in cache. Cannot compare.",
                    file=sys.stderr,
                )
                print(
                    "Use --list to see available licenses or --refresh to update cache.",
                    file=sys.stderr,
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

        print(f"\nUsing license: {title} ({spdxId})")  # Essential Output

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
        if not fieldsDataList:
            verbose_print(
                "Warning: fields.yml data not found in cache. Cannot provide placeholder descriptions."
            )
            fieldsData = {}
        else:
            fieldsData = {
                item["name"].lower(): item
                for item in fieldsDataList
                if isinstance(item, dict) and item.get("name")
            }

        foundPlaceholders = FindPlaceholders(body)
        missingArgs: bool = False
        verbose_print("Checking required placeholders:")
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
                # This warning is useful, make it verbose
                verbose_print(
                    f"  Warning: Placeholder [{ph}] ({description}) found, but no value provided via {argSuggestion}."
                )
                missingArgs = True
        if missingArgs:
            verbose_print(
                "  License generated, but placeholders might remain unfilled."
            )

        # --- Fill and Output ---
        filledLicense: str = FillLicenseTemplate(body, replacements)
        if args.output:
            try:
                outputPath: Path = Path(args.output)
                with open(outputPath, "w", encoding="utf-8") as f:
                    f.write(filledLicense)
                # Essential Output
                print(f"\nLicense successfully written to '{args.output}'")
            except IOError as e:
                # Essential Error
                print(
                    f"\nError writing to output file '{args.output}': {e}",
                    file=sys.stderr,
                )
                return 1
        else:
            # Essential Output
            print("\n--- Filled License Text ---")
            print(filledLicense)
            print("--- End License Text ---\n")
        return 0

    # If no action argument was given
    # Essential Error/Help
    print(
        "\nError: No action specified. Use --list, --detailed-list, --info, --show-placeholders, --compare, --find, or --license.",
        file=sys.stderr,
    )
    parser.print_help(file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
