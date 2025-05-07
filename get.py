import argparse
import base64
import json  # For caching
import os
import re
import sys  # Needed for stderr
import textwrap
from collections import OrderedDict  # Added for CompareLicenses
from datetime import datetime
from pathlib import Path

import requests
import yaml  # Requires PyYAML

# Rich for progress bar and console output
try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table  # Added for CompareLicenses
except ImportError:
    # Fallback print if rich is not installed
    print(
        "Error: 'rich' library not found. Please install it: pip install rich",
        file=sys.stderr,
    )

    # Define a dummy Console and Progress if rich is missing to avoid NameErrors
    class DummyConsole:
        def print(self, *args, **kwargs):
            # Simple print, ignoring style arguments
            file = kwargs.get("file", sys.stdout)
            sep = kwargs.get("sep", " ")
            end = kwargs.get("end", "\n")
            print(*args, file=file, sep=sep, end=end)

    class DummyProgress:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def add_task(self, *args, **kwargs):
            return 0  # Dummy task ID

        def update(self, *args, **kwargs):
            pass

        def advance(self, *args, **kwargs):
            pass

    class DummyTable:  # Basic table mock
        def __init__(self, *args, **kwargs):
            pass

        def add_column(self, *args, **kwargs):
            pass

        def add_row(self, *args, **kwargs):
            pass

        def add_section(self, *args, **kwargs):  # Mock for add_section
            pass

    Console = DummyConsole
    Progress = DummyProgress
    Table = DummyTable  # Use DummyTable if rich is not available
    # No need to define columns if Progress is dummy

# --- Constants ---
GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"
DATA_PATH: str = "_data"
CACHE_FILENAME: str = "license_cache.json"

# --- Map standard placeholders to command-line arguments ---
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

# --- Key Rules for Comparison Table (Demo 5 Choice) ---
KEY_RULES_FOR_COMPARISON = OrderedDict(
    [
        ("Commercial use", "commercial-use"),
        ("State changes", "document-changes"),
        ("Disclose source", "disclose-source"),
        ("Same license", "same-license"),
        ("License & copyright notice", "include-copyright"),
        ("Liability", "liability"),
        ("Warranty", "warranty"),
        ("Trademark use", "trademark-use"),
        (
            "Patent use (Perm)",  # Label for column
            "patent-use_perm",  # Special key to check permissions for 'patent-use'
        ),
        (
            "Patent use (Lim)",  # Label for column
            "patent-use_lim",  # Special key to check limitations for 'patent-use'
        ),
    ]
)


# --- Global Console Instance ---
# Use stderr for status/errors by default, stdout for final license text
console = Console(stderr=True, highlight=False)  # Use stderr for status messages
stdout_console = Console(highlight=False)  # Use stdout for final license output

# --- Verbose Print Helper ---
_VERBOSE = False


def VerbosePrint(*args, **kwargs):
    """
    Prints only if the verbose flag is set.

    Outputs to stderr to separate status messages from potential stdout data.

    Parameters
    ----------
    *args : tuple
        Arguments to pass to the console.print function.
    **kwargs : dict
        Keyword arguments to pass to the console.print function.
    """

    # Print if verbose flag is True
    if _VERBOSE:
        console.print(*args, **kwargs)


# --- Helper Functions (GitHub API, Fetching, Parsing) ---


def GetGithubApi(endpoint: str) -> dict | list | None:
    """
    Makes a GET request to the GitHub API.

    Parameters
    ----------
    endpoint : str
        The API endpoint to request (e.g., /repos/owner/repo/contents/path).

    Returns
    -------
    dict | list | None
        The JSON response from the API, or None if an error occurred.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    githubToken = os.environ.get("GITHUB_TOKEN")

    # Add authorization header if token exists
    if githubToken:
        headers["Authorization"] = f"token {githubToken}"

    url: str = f"{GITHUB_API_URL}{endpoint}"

    try:
        response = requests.get(url, headers=headers, timeout=15)  # Add timeout
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

        # Return JSON response
        return response.json()

    except requests.exceptions.Timeout:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] Timeout while fetching from GitHub API ({url})"
        )

        # Return None on error
        return None
    except requests.exceptions.RequestException as e:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] Fetching from GitHub API ({url}): {e}"
        )

        # Print response details if available
        if hasattr(e, "response") and e.response is not None:
            console.print(f"Response Status: {e.response.status_code}")
            console.print(f"Response Body: {e.response.text[:500]}...")

            # Handle specific 403 error for rate limiting
            if e.response.status_code == 403:
                rateLimitInfo = e.response.headers.get("X-RateLimit-Remaining", "N/A")
                console.print(
                    f"[yellow]Hint:[/yellow] Check GitHub API rate limits (Remaining: {rateLimitInfo}) or authentication (set GITHUB_TOKEN)."
                )

        # Return None on error
        return None
    except Exception as e:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] An unexpected error occurred during API call: {e}"
        )

        # Return None on error
        return None


def FetchGithubDirListing(repoPath: str) -> list[dict[str, object]] | None:
    """
    Fetches the list of files in a directory from the GitHub repo API.

    Parameters
    ----------
    repoPath : str
        The path within the repository (e.g., _licenses, _data).

    Returns
    -------
    list[dict[str, object]] | None
        A list of file/directory dictionaries from the API, or None on error.
    """
    # Keep verbose print for the start of the operation
    VerbosePrint(f"Fetching current file list from GitHub ({repoPath})...")
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{repoPath}?ref={BRANCH}"
    data = GetGithubApi(endpoint)

    # Check if data is valid list
    if not data or not isinstance(data, list):
        # Essential Error (unless cache exists later)
        console.print(
            f"[bold red]Error:[/bold red] Could not fetch or parse directory listing for {repoPath}"
        )

        # Return None on error
        return None

    # Return valid list data
    return data


def FetchFileContent(downloadUrl: str) -> str | None:
    """
    Fetches the content of a single file from a direct download URL.

    Parameters
    ----------
    downloadUrl : str
        The URL provided by the GitHub API to download the raw file content.

    Returns
    -------
    str | None
        The text content of the file, or None on error.
    """
    try:
        response = requests.get(downloadUrl, timeout=10)  # Add timeout
        response.raise_for_status()  # Raise HTTPError for bad responses

        # Return text content
        return response.text

    except requests.exceptions.Timeout:
        # Essential Error for the specific file fetch
        console.print(
            f"\n[bold red]Error:[/bold red] Timeout fetching content from {downloadUrl}"
        )

        # Return None on error
        return None
    except requests.exceptions.RequestException as e:
        # Essential Error for the specific file fetch
        console.print(
            f"\n[bold red]Error:[/bold red] Fetching content from {downloadUrl}: {e}"
        )

        # Return None on error
        return None
    except Exception as e:
        # Essential Error for the specific file fetch
        console.print(
            f"\n[bold red]Error:[/bold red] An unexpected error occurred fetching content from {downloadUrl}: {e}"
        )

        # Return None on error
        return None


# --- Parsing Functions ---


def ParseLicenseFile(filename: str, fileContent: str) -> dict[str, object] | None:
    """
    Parses SPDX ID, full front matter, and body from license file content.

    Parameters
    ----------
    filename : str
        The name of the license file being parsed (for context in messages).
    fileContent : str
        The raw text content of the license file.

    Returns
    -------
    dict[str, object] | None
        A dictionary containing 'spdx_id', 'front_matter' (dict), and 'body' (str),
        or None if the SPDX ID cannot be determined.
    """
    spdxId: str | None = None
    frontMatter: dict[str, object] = {}
    body: str = fileContent.strip()

    # Check for YAML front matter delimiters
    if fileContent.strip().startswith("---"):
        parts = fileContent.split("---", 2)

        # Ensure correct structure (empty string, front matter, body)
        if len(parts) >= 3:
            frontMatterRaw: str = parts[1].strip()
            body = parts[2].strip()

            try:
                # Load YAML, allow empty results, default to empty dict
                frontMatter = yaml.safe_load(frontMatterRaw) or {}

                # Validate parsed front matter is a dictionary
                if not isinstance(frontMatter, dict):
                    VerbosePrint(
                        f"[yellow]Warning:[/yellow] Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}

                # Get SPDX ID from parsed data first
                spdxId = frontMatter.get("spdx-id")

            except yaml.YAMLError as e:
                VerbosePrint(
                    f"[yellow]Warning:[/yellow] YAML parse error for {filename}: {e}. Fallback."
                )
                frontMatter = {}

                # Fallback regex search if YAML fails
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()

        else:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Malformed front matter in {filename}."
            )
            # Fallback: Guess SPDX ID from filename if not found yet
            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)
    else:
        VerbosePrint(f"[yellow]Warning:[/yellow] No front matter '---' in {filename}.")
        spdxId = GuessSpdxFromFilename(filename)

    # Final check and fallback for SPDX ID from parsed front matter
    if not spdxId and "spdx-id" in frontMatter:
        spdxId = frontMatter["spdx-id"]

    # If SPDX ID is still missing, cannot proceed
    if not spdxId:
        console.print(
            f"[bold red]Error:[/bold red] Could not determine SPDX ID for {filename}. Skipping."
        )

        # Return None if SPDX ID missing
        return None

    # Ensure basic fields exist in frontMatter for consistency in cache structure
    frontMatter.setdefault("spdx-id", spdxId)
    frontMatter.setdefault("title", spdxId)  # Fallback title
    frontMatter.setdefault("nickname", None)
    frontMatter.setdefault("description", None)
    frontMatter.setdefault("permissions", [])
    frontMatter.setdefault("conditions", [])
    frontMatter.setdefault("limitations", [])

    # Return parsed data
    return {"spdx_id": spdxId, "front_matter": frontMatter, "body": body}


def GuessSpdxFromFilename(filename: str) -> str | None:
    """
    Guesses SPDX ID from filename.

    Parameters
    ----------
    filename : str
        The filename (e.g., 'mit.txt').

    Returns
    -------
    str | None
        The guessed SPDX ID, or None if the format seems invalid.
    """
    spdxIdGuess: str = os.path.splitext(filename)[0]

    # Basic check, SPDX allows letters, numbers, ., -, +
    if re.match(r"^[A-Za-z0-9.\-\+]+$", spdxIdGuess):

        # Return the guess
        return spdxIdGuess
    else:
        VerbosePrint(
            f"[yellow]Warning:[/yellow] Filename {filename} doesn't look like a typical SPDX ID format. Cannot reliably guess."
        )

        # Return None if format invalid
        return None


def ParseDataFile(filename: str, fileContent: str) -> object | None:
    """
    Parses YAML data file content.

    Parameters
    ----------
    filename : str
        The name of the data file (for context in messages).
    fileContent : str
        The raw YAML content of the file.

    Returns
    -------
    object | None
        The parsed Python object (usually dict or list), or None on error.
    """
    try:
        data = yaml.safe_load(fileContent)

        # Return parsed data
        return data
    except yaml.YAMLError as e:
        # Essential Error for this file
        console.print(
            f"[bold red]Error:[/bold red] parsing YAML data file {filename}: {e}"
        )

        # Return None on error
        return None
    except Exception as e:
        # Essential Error for this file
        console.print(
            f"[bold red]Error:[/bold red] An unexpected error occurred parsing data file {filename}: {e}"
        )

        # Return None on error
        return None


# --- Caching Functions ---


def LoadCache(cacheFilePath: Path) -> dict[str, object]:
    """
    Loads the license cache from a JSON file.

    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.

    Returns
    -------
    dict[str, object]
        The loaded cache data, or an empty dictionary if loading fails or file is empty.
    """

    # Check if cache file exists
    if cacheFilePath.exists():

        try:
            with open(cacheFilePath, "r", encoding="utf-8") as f:
                content = f.read()

                # Return empty dict if file is empty
                if not content:
                    VerbosePrint(
                        f"[yellow]Warning:[/yellow] Cache file {cacheFilePath} is empty. Starting fresh."
                    )
                    return {}

                # Return parsed JSON data
                return json.loads(content)

        except (IOError, json.JSONDecodeError) as e:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Could not load or parse cache file {cacheFilePath}: {e}. Starting fresh."
            )

            # Return empty dict on error
            return {}
        except Exception as e:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] An unexpected error occurred loading cache: {e}"
            )

            # Return empty dict on error
            return {}

    # Return empty dict if file doesn't exist
    return {}


def SaveCache(cacheFilePath: Path, cacheData: dict[str, object]) -> None:
    """
    Saves the license cache to a JSON file.

    Parameters
    ----------
    cacheFilePath : Path
        The path where the cache file should be saved.
    cacheData : dict[str, object]
        The cache data dictionary to save.
    """
    try:
        # Ensure parent directory exists
        cacheFilePath.parent.mkdir(parents=True, exist_ok=True)

        # Write JSON data to file
        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
        VerbosePrint(f"Cache saved to {cacheFilePath}")

    except IOError as e:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] Could not save cache file {cacheFilePath}: {e}"
        )
    except Exception as e:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] An unexpected error occurred saving cache: {e}"
        )


def UpdateAndLoadLicenseCache(
    cacheFilePath: Path, forceRefresh: bool = False
) -> dict[str, object]:
    """
    Loads cache, checks GitHub for updates, fetches necessary files using a
    progress bar, updates cache, saves cache, and returns the up-to-date cache data.

    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.
    forceRefresh : bool, optional
        If True, ignores the existing cache and fetches all data anew. Default is False.

    Returns
    -------
    dict[str, object]
        The up-to-date cache data dictionary. Returns an empty dictionary if
        initial GitHub listing fails and no cache exists.
    """
    VerbosePrint("Loading cache...")
    cachedData = LoadCache(cacheFilePath) if not forceRefresh else {}
    if forceRefresh:
        VerbosePrint("Cache refresh forced.")

    needsSave = False
    processedDataFiles = {}
    dataFilesToFetch = []
    licenseFilesToFetch = []

    # Define common progress bar columns
    progressColumns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),  # Use full width
        MofNCompleteColumn(),  # Show count M/N
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ]

    # --- Check Data Files ---
    # FetchGithubDirListing prints verbose message inside
    githubDataFiles = FetchGithubDirListing(DATA_PATH)

    # Handle failure to fetch directory listing
    if githubDataFiles is None:
        VerbosePrint(
            "[yellow]Warning:[/yellow] Failed to fetch data file list. Using potentially stale cached data."
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

        # Compare with cache to find changes
        for name, ghInfo in currentGithubDataFiles.items():
            cacheKey = f"data:{name}"
            cachedEntry = cachedData.get(cacheKey)
            gh_sha = ghInfo.get("sha")

            # Mark for fetch if SHA missing or different from cache
            if not gh_sha or not (
                cachedEntry
                and isinstance(cachedEntry, dict)
                and cachedEntry.get("sha") == gh_sha
            ):
                dataFilesToFetch.append(ghInfo)
                needsSave = True
                if cachedEntry:
                    VerbosePrint(
                        f"  Detected change in data file {name} (SHA mismatch or missing)."
                    )
                else:
                    VerbosePrint(f"  Detected new data file {name}.")
            else:
                # Keep valid cached entry
                processedDataFiles[cacheKey] = cachedEntry

        # Identify deleted data files
        cachedDataFilenames = {
            k.split(":", 1)[1] for k in cachedData.keys() if k.startswith("data:")
        }
        deletedDataFilenames = cachedDataFilenames - set(currentGithubDataFiles.keys())
        if deletedDataFilenames:
            VerbosePrint(
                f"  Detected deleted data files: {', '.join(deletedDataFilenames)}"
            )
            needsSave = True

    # --- Check License Files ---
    # FetchGithubDirListing prints verbose message inside
    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)
    processedLicenses = {}

    # Handle failure to fetch directory listing
    if githubLicenseFiles is None:
        VerbosePrint(
            "[yellow]Warning:[/yellow] Failed to fetch license file list. Using potentially stale cached licenses."
        )
        # Keep existing cached licenses if list fetch failed
        for key, val in cachedData.items():
            if not key.startswith("data:"):
                processedLicenses[key] = val
    else:
        currentGithubLicenseFiles = {
            item["name"]: item for item in githubLicenseFiles if isinstance(item, dict)
        }

        # Compare with cache to find changes
        for name, ghInfo in currentGithubLicenseFiles.items():
            # Skip non-txt files
            if not name.endswith(".txt"):
                continue

            cachedEntry = None
            cachedSpdxLower = None
            # Find corresponding cached entry by filename
            for key, val in cachedData.items():
                if (
                    not key.startswith("data:")
                    and isinstance(val, dict)
                    and val.get("filename") == name
                ):
                    cachedEntry = val
                    cachedSpdxLower = key
                    break

            gh_sha = ghInfo.get("sha")

            # Mark for fetch if SHA missing or different from cache
            if not gh_sha or not (cachedEntry and cachedEntry.get("sha") == gh_sha):
                licenseFilesToFetch.append(ghInfo)
                needsSave = True
                if cachedEntry:
                    VerbosePrint(
                        f"  Detected change in {name} (SHA mismatch or missing)."
                    )
                else:
                    VerbosePrint(f"  Detected new file {name}.")
            else:
                # Check if SPDX ID in cache matches the key, correct if needed
                spdxIdInCache = cachedEntry.get("spdx_id")
                if spdxIdInCache and spdxIdInCache.lower() != cachedSpdxLower:
                    VerbosePrint(f"  SPDX ID mismatch for cached {name}. Updating key.")
                    processedLicenses[spdxIdInCache.lower()] = cachedEntry
                    needsSave = True  # Need to save cache with corrected key
                else:
                    processedLicenses[cachedSpdxLower] = cachedEntry  # Keep valid entry

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
            VerbosePrint(
                f"  Detected deleted license files: {', '.join(deletedLicenseFilenames)}"
            )
            needsSave = True

    # --- Fetch content for new/changed files using Progress Bar ---
    totalFilesToFetch = len(dataFilesToFetch) + len(licenseFilesToFetch)

    # Display progress bar only if there are files to fetch
    if totalFilesToFetch > 0:
        console.print()  # Add newline before progress bar
        with Progress(
            *progressColumns, console=console, transient=False
        ) as progress:  # Use stderr console, make bar persistent
            fetchTask = progress.add_task(
                "[cyan]Syncing cache...", total=totalFilesToFetch
            )

            # Fetch data files
            for ghInfo in dataFilesToFetch:
                filename = ghInfo.get("name", "unknown")
                progress.update(
                    fetchTask, description=f"[cyan]Fetching data: {filename}"
                )
                content = FetchFileContent(ghInfo.get("download_url"))

                # Process fetched content
                if content:
                    parsedData = ParseDataFile(filename, content)
                    if parsedData is not None:
                        # Store parsed data and SHA in processed dict
                        processedDataFiles[f"data:{filename}"] = {
                            "sha": ghInfo.get("sha", ""),
                            "content": parsedData,
                        }
                    elif f"data:{filename}" in cachedData:
                        # If parsing failed but it was cached, keep old version
                        VerbosePrint(
                            f"  Failed to parse {filename}, keeping old cached version."
                        )
                        processedDataFiles[f"data:{filename}"] = cachedData[
                            f"data:{filename}"
                        ]
                else:
                    VerbosePrint(f"  Failed to fetch data file {filename}.")

                # Advance progress after processing each file
                progress.advance(fetchTask)

            # Fetch license files
            for ghInfo in licenseFilesToFetch:
                filename = ghInfo.get("name", "unknown.txt")
                progress.update(
                    fetchTask, description=f"[cyan]Fetching license: {filename}"
                )
                content = FetchFileContent(ghInfo.get("download_url"))

                # Process fetched content
                if content:
                    parsedData = ParseLicenseFile(filename, content)
                    if parsedData:
                        spdxLower = parsedData["spdx_id"].lower()
                        fm = parsedData["front_matter"]
                        # Store all required fields + full content in processed dict
                        processedLicenses[spdxLower] = {
                            "spdx_id": parsedData["spdx_id"],
                            "title": fm.get("title", parsedData["spdx_id"]),
                            "filename": filename,
                            "sha": ghInfo.get("sha", ""),
                            "nickname": fm.get("nickname"),
                            "description": fm.get("description"),
                            "permissions": fm.get("permissions", []),
                            "conditions": fm.get("conditions", []),
                            "limitations": fm.get("limitations", []),
                            "file_content_cached": content,  # Cache the full file content
                        }
                    elif filename in {
                        v.get("filename")
                        for k, v in cachedData.items()
                        if not k.startswith("data:")
                    }:
                        # If parsing failed but it was cached, keep old version
                        VerbosePrint(
                            f"  Failed to parse {filename}, keeping old cached version."
                        )
                        for key, val in cachedData.items():
                            if (
                                not key.startswith("data:")
                                and isinstance(val, dict)
                                and val.get("filename") == filename
                            ):
                                processedLicenses[key] = val
                                break
                else:
                    VerbosePrint(f"  Failed to fetch content for {filename}.")

                # Advance progress after processing each file
                progress.advance(fetchTask)
        console.print()  # Add newline after progress bar

    # Remove deleted licenses explicitly after processing fetched ones
    keysToRemoveFromProcessed = [
        key
        for key, val in processedLicenses.items()
        if not key.startswith("data:")
        and val.get("filename") in deletedLicenseFilenames
    ]
    for key in keysToRemoveFromProcessed:
        processedLicenses.pop(key, None)

    finalCacheData = {**processedDataFiles, **processedLicenses}  # Combine

    # Save cache if changes were detected or forced
    if needsSave or deletedDataFilenames or deletedLicenseFilenames or forceRefresh:
        SaveCache(cacheFilePath, finalCacheData)  # SaveCache prints verbose message
    else:
        VerbosePrint("Cache is up-to-date.")

    # Return the combined, updated cache data
    return finalCacheData


# --- Display and Filling Functions ---


def FindPlaceholders(templateBody: str) -> set[str]:
    """
    Finds all unique placeholders like [placeholder] in the text.

    Parameters
    ----------
    templateBody : str
        The text to search for placeholders.

    Returns
    -------
    set[str]
        A set of unique placeholder names found (without brackets).
    """
    pattern: str = r"\[([^\]]+)\]"
    placeholders: list[str] = re.findall(pattern, templateBody)

    # Return unique placeholders
    return set(placeholders)


def FillLicenseTemplate(templateBody: str, replacements: dict[str, str]) -> str:
    """
    Fills placeholders in the license template body.

    Parameters
    ----------
    templateBody : str
        The license template body text.
    replacements : dict[str, str]
        A dictionary where keys are placeholder names (without brackets)
        and values are the strings to substitute.

    Returns
    -------
    str
        The license text with placeholders filled.
    """
    filledText: str = templateBody

    # Iterate through replacements and substitute
    for placeholder, value in replacements.items():
        # Ensure placeholder format consistency (e.g., always '[placeholder]')
        phFormatted: str = f"[{placeholder.strip('[]')}]"
        filledText = filledText.replace(phFormatted, str(value))  # Use str() for safety

    # Return text with substitutions made
    return filledText


def ListLicenses(licensesData: dict[str, object]) -> None:
    """
    Prints a simple list of available licenses from cached data.
    (Demo 1: Option 1)

    Parameters
    ----------
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    # Filter out non-license entries (data files)
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]

    # Check if any licenses were found
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    # Print header
    console.print("\n[bold]Available Licenses (SPDX ID: Title):[/bold]")
    console.print("[dim]" + ("-" * 50) + "[/dim]")

    # Sort and print license info
    sortedKeys: list[str] = sorted(licenseKeys)
    for spdxLower in sortedKeys:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        console.print(f"  [cyan]{spdx:<25}[/cyan] : {title}")

    # Print footer
    console.print("[dim]" + ("-" * 50) + "[/dim]")


def PrintDetailedList(licensesData: dict[str, object]) -> None:
    """
    Prints a detailed list of licenses using cached basic info and rule labels.
    (Demo 2: Option 1)

    Parameters
    ----------
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    # Filter out non-license entries
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]

    # Check if any licenses were found
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    # Load rules data from cache, handle missing case
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    rulesMap = {}
    if not rulesData:
        VerbosePrint(
            "[yellow]Warning:[/yellow] rules.yml data not found in cache. Rule labels may be missing."
        )
    else:
        for category in ["permissions", "conditions", "limitations"]:
            # Create a map of tag -> rule dict for efficient lookup
            rulesMap[category] = {
                rule.get("tag"): rule
                for rule in rulesData.get(category, [])
                if isinstance(rule, dict) and rule.get("tag")
            }

    # Print header
    console.print("\n[bold]--- Detailed License List (from cache) ---[/bold]")

    # Sort and iterate through licenses
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

        # Print core license info
        console.print(f"\n[bold cyan]SPDX ID:[/bold cyan] {spdx}")
        console.print(f"[bold]Title:[/bold] {title}")
        if nickname:
            console.print(f"[italic]Nickname:[/italic] {nickname}")

        # Print truncated description
        truncated_desc = textwrap.shorten(
            description or "", width=100, placeholder="..."
        )
        console.print(f"[bold]Description:[/bold] {truncated_desc}")

        # Helper function to get rule labels
        def GetRuleLabels(tags: list[str], category: str) -> list[str]:
            labels = []
            catRulesMap = rulesMap.get(category, {})
            for tag in tags:
                ruleInfo = catRulesMap.get(tag)
                labels.append(
                    ruleInfo.get("label", tag) if ruleInfo else tag
                )  # Use tag as fallback
            return sorted(labels)  # Sort labels alphabetically

        # Get and print rule labels
        perm_labels = GetRuleLabels(perms_tags, "permissions")
        cond_labels = GetRuleLabels(conds_tags, "conditions")
        lim_labels = GetRuleLabels(lims_tags, "limitations")

        console.print(
            f"[bold green]Permissions[/bold green] ([blue]{len(perm_labels)}[/blue]): {', '.join(perm_labels) if perm_labels else '[dim]None[/dim]'}"
        )
        console.print(
            f"[bold yellow]Conditions[/bold yellow] ([blue]{len(cond_labels)}[/blue]): {', '.join(cond_labels) if cond_labels else '[dim]None[/dim]'}"
        )
        console.print(
            f"[bold red]Limitations[/bold red] ([blue]{len(lim_labels)}[/blue]): {', '.join(lim_labels) if lim_labels else '[dim]None[/dim]'}"
        )

        # Print separator between entries
        if i < len(sortedKeys) - 1:
            console.print("[dim]---[/dim]")

    # Print final footer
    console.print("\n[bold]--- End Detailed License List ---[/bold]")


def GetFullLicenseData(
    spdxIdLower: str, licensesData: dict[str, object]
) -> dict[str, object] | None:
    """
    Retrieves full license data, fetching from GitHub if not fully cached.

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license to retrieve.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.

    Returns
    -------
    dict[str, object] | None
        A dictionary containing the full parsed license data ('spdx_id',
        'front_matter', 'body'), or None if retrieval fails.
    """
    basicInfo = licensesData.get(spdxIdLower)
    if not basicInfo:
        # Essential Error
        console.print(
            f"[bold red]Error:[/bold red] Basic info for {spdxIdLower.upper()} not found in cache."
        )
        return None

    content = basicInfo.get("file_content_cached")
    fullLicenseData = None

    # Try parsing cached content first
    if content:
        VerbosePrint(
            f"Using cached content for {basicInfo.get('filename', spdxIdLower.upper())}."
        )
        fullLicenseData = ParseLicenseFile(
            basicInfo.get("filename", "unknown"), content
        )
        if not fullLicenseData:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Failed to parse cached content for {basicInfo.get('filename', spdxIdLower.upper())}. Re-fetching."
            )
            content = None  # Force re-fetch

    # Fetch if content wasn't cached or failed to parse
    if not content:
        filename = basicInfo.get("filename")
        if not filename:
            # Essential Error
            console.print(
                f"[bold red]Error:[/bold red] Filename missing for {spdxIdLower.upper()} in cache. Cannot fetch full info."
            )
            return None

        VerbosePrint(f"Fetching full content for {filename} from GitHub...")

        # Fetch the directory listing to find the download URL.
        # Optimization TODO: Store download_url in the cache.
        githubFiles = FetchGithubDirListing(LICENSES_PATH)
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if isinstance(item, dict) and item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            # Essential Error
            console.print(
                f"[bold red]Error:[/bold red] Could not find download URL for {filename}. Cannot fetch content."
            )
            return None

        # Fetch the actual content
        content = FetchFileContent(downloadUrl)
        if content:
            fullLicenseData = ParseLicenseFile(filename, content)
            # Note: We don't update the cache here automatically, rely on --refresh
        else:
            # FetchFileContent prints essential error
            return None

    # Final check if we got the data
    if not fullLicenseData:
        # Error message already printed by ParseLicenseFile if it failed
        console.print(
            f"[bold red]Error:[/bold red] Failed to get full license data for {spdxIdLower.upper()}."
        )
        return None

    # Return the full parsed data
    return fullLicenseData


def DisplayLicenseInfo(spdxIdLower: str, licensesData: dict[str, object]) -> None:
    """
    Prints the formatted metadata for a license using GetFullLicenseData.
    (Demo 3: Option 1)

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license to display.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)
    if not fullLicenseData:
        # Error message already printed by GetFullLicenseData
        return

    fm: dict[str, object] = fullLicenseData.get("front_matter", {})
    spdxId: str = fullLicenseData.get("spdx_id", "N/A")
    title: str = fm.get("title", "N/A")
    body = fullLicenseData.get("body", "")

    # Load rules and fields data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    # Create lookup map for fields, using lowercase name as key
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    # Print information header
    console.print(f"\n[bold]--- License Information: {title} ({spdxId}) ---[/bold]")

    # Print optional nickname
    if fm.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {fm['nickname']}")

    # Helper function for printing text blocks
    def PrintTextBlock(label: str, text: str | None) -> None:
        if text:
            console.print(f"\n[bold]{label}:[/bold]")
            # Use console.print for automatic wrapping
            console.print(textwrap.indent(text, "  "))

    # Print standard text blocks
    PrintTextBlock("Description", fm.get("description"))
    PrintTextBlock("How to Apply", fm.get("how"))

    # Helper function for printing rule lists with labels
    def PrintRulesWithLabels(
        label: str, key: str, rulesConfig: dict, color: str
    ) -> None:
        ruleTags = fm.get(key, [])
        configRules = rulesConfig.get(key, [])
        # Create lookup map for rules in this category
        configRulesMap = {
            rule.get("tag"): rule
            for rule in configRules
            if isinstance(rule, dict) and rule.get("tag")
        }

        # Print rules if they exist
        if ruleTags and isinstance(ruleTags, list):
            console.print(f"\n[bold {color}]{label}:[/bold {color}]")
            sorted_tags = sorted(ruleTags)  # Sort tags for consistent order
            for tag in sorted_tags:
                ruleInfo = configRulesMap.get(tag)
                # Print label and tag, or just tag if label not found
                if ruleInfo and ruleInfo.get("label"):
                    console.print(
                        f"  - [bold {color}]{ruleInfo['label']}[/bold {color}] ([dim]{tag}[/dim])"
                    )
                    # Also print rule description (shortened)
                    if ruleInfo.get("description"):
                        console.print(
                            f"    [dim i]{textwrap.shorten(ruleInfo['description'], width=80, placeholder='...')}[/dim i]"
                        )
                else:
                    console.print(f"  - {tag} ([yellow]Label not found[/yellow])")

    # Print rule lists
    PrintRulesWithLabels("Permissions", "permissions", rulesData, "green")
    PrintRulesWithLabels("Conditions", "conditions", rulesData, "yellow")
    PrintRulesWithLabels("Limitations", "limitations", rulesData, "red")

    # Print 'using' examples if available
    if (
        fm.get("using") and isinstance(fm["using"], dict) and fm["using"]
    ):  # Check if dict is not empty
        console.print("\n[bold]Notable Projects Using This License:[/bold]")
        for project, url in fm["using"].items():
            console.print(f"  - {project}: {url}")

    # Print optional note
    PrintTextBlock("Note", fm.get("note"))

    # Find and display placeholders
    placeholders = FindPlaceholders(body)
    if placeholders:
        console.print("\n[bold]Placeholders in Body:[/bold]")
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
            console.print(f"  - [bold magenta][{ph}][/bold magenta]")
            console.print(f"    [dim]Description:[/dim] {description}")
            console.print(f"    [dim]Argument:[/dim] {argSuggestion}{defaultInfo}")
    else:
        console.print("\n[bold]Placeholders in Body:[/bold] [dim](None detected)[/dim]")

    # Print footer
    console.print("\n[bold]--- End License Information ---[/bold]")


def DisplayLicenseSummaryAfterWrite(
    spdxIdLower: str,
    licensesData: dict[str, object],
    missingArgs: bool,
    foundPlaceholders: set[str],
    outputPath: Path,  # Added outputPath for the confirmation message
) -> None:
    """
    Prints a summary of the license metadata after writing the file.
    Excludes 'How to Apply' and 'Notable Projects'.
    Conditionally shows unfilled placeholders.
    (Demo 6: Option 1)

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    missingArgs : bool
        True if placeholders were detected but not filled by user arguments.
    foundPlaceholders : set[str]
        The set of placeholders found in the license body.
    outputPath : Path
        The path where the license was written.
    """
    # Essential Output: Confirmation message first
    console.print(f"\nLicense successfully written to '[green]{outputPath}[/green]'")

    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)
    if not fullLicenseData:
        return  # Error message already printed

    fm: dict[str, object] = fullLicenseData.get("front_matter", {})
    spdxId: str = fullLicenseData.get("spdx_id", "N/A")
    title: str = fm.get("title", "N/A")

    # Load rules and fields data from cache
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    console.print(f"\n[bold]--- License Summary: {title} ({spdxId}) ---[/bold]")

    if fm.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {fm['nickname']}")

    def PrintTextBlock(label: str, text: str | None) -> None:
        if text:
            console.print(f"\n[bold]{label}:[/bold]")
            console.print(textwrap.indent(text, "  "))

    PrintTextBlock("Description", fm.get("description"))
    # Excluded: How to Apply

    def PrintRulesWithLabels(
        label: str, key: str, rulesConfig: dict, color: str
    ) -> None:
        ruleTags = fm.get(key, [])
        configRules = rulesConfig.get(key, [])
        configRulesMap = {
            rule.get("tag"): rule
            for rule in configRules
            if isinstance(rule, dict) and rule.get("tag")
        }
        if ruleTags and isinstance(ruleTags, list):
            console.print(f"\n[bold {color}]{label}:[/bold {color}]")
            sorted_tags = sorted(ruleTags)
            for tag in sorted_tags:
                ruleInfo = configRulesMap.get(tag)
                if ruleInfo and ruleInfo.get("label"):
                    console.print(
                        f"  - [bold {color}]{ruleInfo['label']}[/bold {color}] ([dim]{tag}[/dim])"
                    )
                else:
                    console.print(f"  - {tag} ([yellow]Label not found[/yellow])")

    PrintRulesWithLabels("Permissions", "permissions", rulesData, "green")
    PrintRulesWithLabels("Conditions", "conditions", rulesData, "yellow")
    PrintRulesWithLabels("Limitations", "limitations", rulesData, "red")

    # Excluded: Notable Projects

    PrintTextBlock("Note", fm.get("note"))

    # Conditionally show placeholders
    if (
        missingArgs and foundPlaceholders
    ):  # Use foundPlaceholders (originally unfilledPlaceholders)
        console.print(
            "\n[bold yellow]Warning: Some placeholders were not filled:[/bold yellow]"
        )
        for ph in sorted(
            list(foundPlaceholders)
        ):  # Iterate over the set of *unfilled* placeholders
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
            # Determine if it was year and defaulted, or just missing
            if ph_lower in ["year", "yyyy"]:
                # This assumes 'year' and 'yyyy' are always in replacements (defaulted if not provided)
                # So if they are in foundPlaceholders, it means they were *not* in the provided args for fill
                # But the logic in main() for missingArgs is about placeholders *not in replacements dict*.
                # The description for Demo 6 implies it should show which defaulted.
                # The existing logic in `main` for `unfilledPlaceholders` passed here correctly lists those
                # for which no CLI arg was provided by the user to `replacements`.
                # If 'year'/'yyyy' is in `unfilledPlaceholders`, it means no -y was given AND it was not defaulted *prior* to this function call
                # However, 'year' *is* defaulted in main before `FillLicenseTemplate`
                # So `unfilledPlaceholders` here should represent those that truly lack a value.
                # For year, if it's in `unfilledPlaceholders`, it means it wasn't resolved by user args.
                # The original script's `unfilledPlaceholders` correctly captures this.
                # If 'year' is listed here, it means the user *didn't* provide -y, and we want to indicate it defaulted.
                # The existing `defaultInfo` logic in `main` for the --show-placeholders might be more relevant here.
                # Let's re-evaluate: `unfilledPlaceholders` is a set of placeholders found in body but *not* in the `replacements` dict
                # before default year is added. The `replacements` dict used for filling *does* include default year.
                # So, if "year" or "yyyy" is in `unfilledPlaceholders` it means CLI arg was not given.
                # And the license template WILL be filled with default year.
                # The warning should reflect that.
                if ph_lower in [
                    "year",
                    "yyyy",
                ]:  # This placeholder was found in body but no specific arg given
                    defaultInfo = " (defaulted to current year)"
                else:  # For other placeholders, if they are here, they are truly unfilled.
                    defaultInfo = " (argument missing)"

            console.print(f"  - [bold magenta][{ph}][/bold magenta]")
            console.print(f"    [dim]Description:[/dim] {description}")
            console.print(f"    [dim]Argument:[/dim] {argSuggestion}{defaultInfo}")

    console.print(
        "[dim]" + ("-" * 50) + "[/dim]"
    )  # Added footer as per Demo 6 description


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """
    Compares licenses using a Key Rule Indicator Table.
    (Demo 5: Option "Key Rule Indicator Table" from Detailed List Demo Option 3)

    Parameters
    ----------
    spdxIdsLower : list[str]
        A list of lowercase SPDX IDs for the licenses to compare.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    # Check if enough licenses are provided
    if len(spdxIdsLower) < 2:
        # Essential Error
        console.print(
            "\n[bold red]Error:[/bold red] Need at least two license SPDX IDs to compare."
        )
        return

    # Get full data for each license to compare
    licensesToCompare = []
    for spdxLower in spdxIdsLower:
        fullLicenseData = GetFullLicenseData(spdxLower, licensesData)
        if fullLicenseData:
            licensesToCompare.append(fullLicenseData)
        else:
            # GetFullLicenseData prints essential error
            VerbosePrint(
                f"Could not get data for {spdxLower.upper()}, skipping from comparison."
            )

    # Check if we have enough valid licenses for comparison
    if len(licensesToCompare) < 2:
        # Essential Error
        console.print(
            "\n[bold red]Error:[/bold red] Cannot perform comparison: Need at least two valid licenses."
        )
        return

    # Load rules data from cache (for tag to category mapping if needed)
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        # Not strictly essential for this format if KEY_RULES_FOR_COMPARISON is self-contained
        # but good for consistency or future enhancements.
        VerbosePrint(
            "[yellow]Warning:[/yellow] rules.yml data not found in cache. Full rule context might be limited."
        )

    # Map tags to their categories for easier lookup (especially for patent-use)
    rulesMapByCategory = {}
    if rulesData:
        for category_name, rules_in_category_list in rulesData.items():
            if category_name in [
                "permissions",
                "conditions",
                "limitations",
            ] and isinstance(rules_in_category_list, list):
                for rule_detail_dict in rules_in_category_list:
                    if isinstance(rule_detail_dict, dict) and "tag" in rule_detail_dict:
                        rulesMapByCategory[rule_detail_dict["tag"]] = category_name

    # Print comparison header
    console.print("\n[bold]--- Comparing Licenses ---[/bold]")
    licenseSpdxDisplayNames = [lic["spdx_id"] for lic in licensesToCompare]
    console.print(
        "Comparing:",
        ", ".join(f"[cyan]{spdx}[/cyan]" for spdx in licenseSpdxDisplayNames),
    )

    # Create and populate the table
    indicator_table = Table(title="Key Rule Indicators")
    indicator_table.add_column("SPDX ID", justify="left", style="cyan", no_wrap=True)

    for label in KEY_RULES_FOR_COMPARISON.keys():
        wrapped_label = textwrap.fill(
            label, width=10, break_long_words=False, break_on_hyphens=False
        )
        indicator_table.add_column(wrapped_label, justify="center")

    for lic_data in licensesToCompare:
        spdx_id_str: str = lic_data.get("spdx_id", "N/A")
        fm = lic_data.get("front_matter", {})
        perms_tags_list = fm.get("permissions", [])
        conds_tags_list = fm.get("conditions", [])
        lims_tags_list = fm.get("limitations", [])

        row_indicators = []
        for label, tag_key in KEY_RULES_FOR_COMPARISON.items():
            has_rule = False
            actual_tag_to_check = tag_key  # Default to the key itself

            if tag_key == "patent-use_perm":
                actual_tag_to_check = "patent-use"
                has_rule = actual_tag_to_check in perms_tags_list
            elif tag_key == "patent-use_lim":
                actual_tag_to_check = "patent-use"
                has_rule = actual_tag_to_check in lims_tags_list
            else:  # For other rules, check based on their known category
                rule_category = rulesMapByCategory.get(actual_tag_to_check)
                if rule_category == "permissions":
                    has_rule = actual_tag_to_check in perms_tags_list
                elif rule_category == "conditions":
                    has_rule = actual_tag_to_check in conds_tags_list
                elif rule_category == "limitations":
                    has_rule = actual_tag_to_check in lims_tags_list
                else:  # Tag not found in rules.yml, check all categories in front matter
                    has_rule = (
                        actual_tag_to_check in perms_tags_list
                        or actual_tag_to_check in conds_tags_list
                        or actual_tag_to_check in lims_tags_list
                    )

            indicator_symbol = (
                "[bold green]✓[/bold green]" if has_rule else "[bold red]X[/bold red]"
            )
            row_indicators.append(indicator_symbol)
        indicator_table.add_row(spdx_id_str, *row_indicators)

    console.print(indicator_table)

    # Print comparison footer
    console.print("\n[bold]--- End Comparison ---[/bold]")


def FindLicenses(
    requireTags: list[str] | None,
    disallowTags: list[str] | None,
    licensesData: dict[str, object],
) -> None:
    """
    Finds licenses matching require/disallow criteria using cached data.

    Parameters
    ----------
    requireTags : list[str] | None
        List of rule tags that must be present.
    disallowTags : list[str] | None
        List of rule tags that must not be present.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    requireTags = requireTags or []
    disallowTags = disallowTags or []

    # Ensure at least one criterion is provided
    if not requireTags and not disallowTags:
        # Essential Error
        console.print(
            "\n[bold red]Error:[/bold red] Please provide at least one --require or --disallow tag for finding licenses."
        )
        return

    # Filter out non-license entries
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    # Load rules data from cache to validate tags
    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        # Essential Error
        console.print(
            "[bold red]Error:[/bold red] rules.yml data not found in cache. Cannot validate find tags."
        )
        return

    # Collect all valid tags from rules data
    allValidTags = set()
    for category in ["permissions", "conditions", "limitations"]:
        allValidTags.update(
            rule.get("tag")
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        )

    # Validate input tags
    invalidRequire = [tag for tag in requireTags if tag not in allValidTags]
    invalidDisallow = [tag for tag in disallowTags if tag not in allValidTags]

    # Report invalid tags if any found
    if invalidRequire or invalidDisallow:
        # Essential Error
        console.print("\n[bold red]Error:[/bold red] Invalid rule tags provided:")
        if invalidRequire:
            console.print(f"  Invalid --require tags: {', '.join(invalidRequire)}")
        if invalidDisallow:
            console.print(f"  Invalid --disallow tags: {', '.join(invalidDisallow)}")
        return

    # Print search criteria header
    console.print("\n[bold]--- Finding Licenses Matching Criteria ---[/bold]")
    console.print(
        "Require:",
        (
            f"[green]{', '.join(requireTags)}[/green]"
            if requireTags
            else "[dim]None[/dim]"
        ),
    )
    console.print(
        "Disallow:",
        f"[red]{', '.join(disallowTags)}[/red]" if disallowTags else "[dim]None[/dim]",
    )
    console.print("[dim]" + ("-" * 50) + "[/dim]")

    # Filter licenses based on criteria
    matches = []
    for spdxLower in licenseKeys:
        data = licensesData[spdxLower]
        # Combine all rules associated with the license
        licenseRules = set(
            data.get("permissions", [])
            + data.get("conditions", [])
            + data.get("limitations", [])
        )

        # Check requirements and disallowals
        meetsRequire = all(tag in licenseRules for tag in requireTags)
        meetsDisallow = not any(tag in licenseRules for tag in disallowTags)

        # Add to matches if all criteria met
        if meetsRequire and meetsDisallow:
            matches.append(data)

    # Print results
    if not matches:
        console.print("No licenses found matching all criteria.")
    else:
        console.print(f"Found {len(matches)} matching license(s):")
        # Sort matches by SPDX ID and print
        for matchData in sorted(matches, key=lambda x: x.get("spdx_id", "")):
            console.print(
                f"  - [cyan]{matchData.get('spdx_id', 'N/A')}[/cyan] ({matchData.get('title', 'N/A')})"
            )

    # Print footer
    console.print("[dim]" + ("-" * 50) + "[/dim]")


# --- Main Execution ---


def main() -> int:
    """
    Main function to parse arguments and execute the requested action.

    Returns
    -------
    int
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        description="Fetch, display info for, compare, find, or fill open source license templates from github/choosealicense.com using local caching.",
        formatter_class=argparse.RawTextHelpFormatter,  # Use RawText to better control epilog formatting
        epilog=textwrap.dedent(
            """\
Examples:
  %(prog)s --list                           List all available licenses (uses cache).
  %(prog)s --detailed-list                  List licenses with key details (uses cache).
  %(prog)s --refresh --list                 Force refresh cache then list licenses (shows progress bar).
  %(prog)s -v --refresh --list              Force refresh cache then list licenses (verbose).
  %(prog)s --info MIT                       Show detailed info (uses cache, fetches full content if needed).
  %(prog)s --show-placeholders NCSA         Show placeholders (uses cache, fetches full content if needed).
  %(prog)s --compare MIT Apache-2.0 GPL-3.0 Compare licenses (uses cache, fetches full content if needed).
  %(prog)s --find --require commercial-use  Find licenses allowing commercial use (uses cache).
  %(prog)s --find --require disclose-source --disallow liability Find licenses (uses cache).
  %(prog)s --license MIT -f "Jane Doe"      Fill license and save to LICENSE, then show summary.
  %(prog)s -l Apache-2.0 -f ACME -o MyLIC   Fill license, save to MyLIC, then show summary.
"""
        ),
    )

    # --- General Arguments ---
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

    # --- Action Group (Mutually Exclusive) ---
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

    # --- Find Arguments ---
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

    # --- Fill Arguments ---
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
        "-o", "--output", help="Output file path. Defaults to 'LICENSE'."
    )  # Changed default

    args = parser.parse_args()
    cacheFilePath = args.cache_file  # Already a Path object

    # Set global verbose flag
    global _VERBOSE
    _VERBOSE = args.verbose

    # Update cache if needed, then load license and data files
    licensesData = UpdateAndLoadLicenseCache(cacheFilePath, args.refresh)
    if not licensesData:
        # Essential Error already printed in function if needed
        console.print(
            "[bold red]Error:[/bold red] Operation failed: Could not load or update license data."
        )
        return 1

    # --- Handle Actions ---

    # List simple (Demo 1: Option 1)
    if args.list:
        ListLicenses(licensesData)
        return 0

    # List detailed (Demo 2: Option 1)
    if args.detailed_list:
        PrintDetailedList(licensesData)
        return 0

    # Find licenses
    if args.find:
        FindLicenses(args.require, args.disallow, licensesData)
        return 0

    # Show license info (Demo 3: Option 1)
    if args.info:
        requestedIdLower: str = args.info.lower()
        # Check if license exists in cache before trying to fetch/display
        if not licensesData.get(requestedIdLower) and not licensesData.get(
            f"data:{requestedIdLower}"
        ):  # also check if it's a data file key
            basicLicenseInfo = None
            for key, value in licensesData.items():
                if (
                    not key.startswith("data:")
                    and isinstance(value, dict)
                    and value.get("spdx_id", "").lower() == requestedIdLower
                ):
                    basicLicenseInfo = (
                        value  # Found by spdx_id in value rather than key
                    )
                    requestedIdLower = key  # Update to use the cache key
                    break
            if not basicLicenseInfo:
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{args.info}' not found in cache."
                )
                console.print(
                    "Use --list to see available licenses or --refresh to update cache."
                )
                return 1
        DisplayLicenseInfo(requestedIdLower, licensesData)
        return 0

    # Show license placeholders (Demo 4: Option 1)
    if args.show_placeholders:
        requestedIdLower = args.show_placeholders.lower()
        # Get full data (includes fetching if necessary)
        fullLicenseData = GetFullLicenseData(requestedIdLower, licensesData)
        if not fullLicenseData:
            # Error message already printed by GetFullLicenseData or initial check
            if not licensesData.get(
                requestedIdLower
            ):  # Specific check if GFLD failed early
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{args.show_placeholders}' not found in cache."
                )
                console.print(
                    "Use --list to see available licenses or --refresh to update cache."
                )
            return 1

        # Get basic info for title/spdx display
        basicInfo = licensesData.get(requestedIdLower, {})
        # Load fields data from cache for descriptions
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        if not fieldsDataList:
            VerbosePrint(
                "[yellow]Warning:[/yellow] fields.yml data not found in cache. Placeholder descriptions unavailable."
            )
            fieldsData = {}
        else:
            fieldsData = {
                item["name"].lower(): item
                for item in fieldsDataList
                if isinstance(item, dict) and item.get("name")
            }

        console.print(
            f"\n[bold]--- Placeholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}) ---[/bold]"
        )  # Essential Output
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
        if not placeholders:
            console.print(
                "  [dim](No standard [placeholder] patterns found)[/dim]"
            )  # Essential Output
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
                console.print(
                    f"  - [bold magenta][{ph}][/bold magenta]"
                )  # Essential Output
                console.print(
                    f"    [dim]Description:[/dim] {description}"
                )  # Essential Output
                console.print(
                    f"    [dim]Argument:[/dim] {argSuggestion}{defaultInfo}"
                )  # Essential Output
        console.print("\n[bold]--- End Placeholder List ---[/bold]")  # Added footer
        return 0

    # Compare licenses (Demo 5: Key Rule Indicator Table)
    if args.compare:
        spdxIdsToCompare = [id.lower() for id in args.compare]
        # Check if all requested licenses are in the cache first (basic info)
        allFoundInCache = True
        for spdxLower in spdxIdsToCompare:
            if spdxLower not in licensesData:
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{spdxLower.upper()}' not found in cache. Cannot compare."
                )
                console.print(
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

    # Fill license (Demo 6: Option 1)
    if args.license:
        requestedLicenseIdLower: str = args.license.lower()
        # Get full data (includes fetching if necessary)
        fullLicenseData = GetFullLicenseData(requestedLicenseIdLower, licensesData)
        if not fullLicenseData:
            # Error message already printed by GetFullLicenseData or initial check
            if not licensesData.get(
                requestedLicenseIdLower
            ):  # Specific check if GFLD failed early
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{args.license}' not found in cache."
                )
                console.print(
                    "Use --list to see available licenses or --refresh to update cache."
                )
            return 1

        title: str = fullLicenseData.get("front_matter", {}).get("title", "N/A")
        spdxId: str = fullLicenseData.get("spdx_id", "N/A")
        body: str = fullLicenseData.get("body", "")

        console.print(
            f"\nUsing license: [bold cyan]{title}[/bold cyan] ({spdxId})"
        )  # Essential Output

        # --- Prepare replacements ---
        currentYear: str = str(datetime.now().year)
        replacements: dict[str, str] = {}  # CLI provided replacements
        final_replacements: dict[str, str] = {}  # Replacements after defaults

        # User provided args first
        if args.fullname:
            replacements["fullname"] = args.fullname
            replacements["name of copyright owner"] = args.fullname
        if args.project:
            replacements["project"] = args.project
        if args.email:
            replacements["email"] = args.email
        if args.projecturl:
            replacements["projecturl"] = args.projecturl
        if args.year:
            replacements["year"] = args.year
            replacements["yyyy"] = args.year

        # Defaults (year is crucial)
        final_replacements["year"] = replacements.get("year", currentYear)
        final_replacements["yyyy"] = replacements.get(
            "yyyy", currentYear
        )  # Ensure yyyy also gets this default or user value

        # Add other user-provided replacements to final_replacements
        for key, value in replacements.items():
            if key not in [
                "year",
                "yyyy",
            ]:  # Avoid overwriting if year was explicitly set
                final_replacements[key] = value

        # --- Check Placeholders ---
        foundPlaceholdersInBody = FindPlaceholders(body)
        unfilledPlaceholdersForWarning: set[str] = set()
        missingArgsFlag: bool = False

        VerbosePrint("Checking required placeholders against provided arguments:")
        for ph_in_body in foundPlaceholdersInBody:
            ph_lower = ph_in_body.lower()
            check_key = ph_lower
            if check_key == "yyyy":
                check_key = "year"
            elif check_key == "name of copyright owner":
                check_key = "fullname"

            # Check if this placeholder was effectively covered by a CLI argument in `replacements`
            # (before defaults were applied to `final_replacements`)
            if (
                check_key not in replacements
            ):  # `replacements` only has what user explicitly passed
                # This placeholder was in the body but no direct CLI arg was given for it
                missingArgsFlag = True
                unfilledPlaceholdersForWarning.add(
                    ph_in_body
                )  # Add original placeholder name
                argSuggestion: str = PLACEHOLDER_TO_ARG_MAP.get(
                    ph_lower, f"'[{ph_in_body}]'"
                )
                VerbosePrint(
                    f"  [yellow]Warning:[/yellow] Placeholder [{ph_in_body}] found, but no value explicitly provided via {argSuggestion}."
                )

        if missingArgsFlag:
            VerbosePrint(
                "  [yellow]License will be generated; some placeholders might use defaults or remain if no default exists.[/yellow]"
            )

        # --- Fill Template using final_replacements (which includes defaults) ---
        filledLicense: str = FillLicenseTemplate(body, final_replacements)

        # --- Determine Output Path ---
        outputPath: Path
        if args.output:
            outputPath = Path(args.output)
        else:
            outputPath = Path("LICENSE")  # Default filename

        # --- Write License File ---
        try:
            outputPath.parent.mkdir(parents=True, exist_ok=True)  # Ensure dir exists
            with open(outputPath, "w", encoding="utf-8") as f:
                f.write(filledLicense + "\n")  # Add trailing newline
            # Confirmation message moved to DisplayLicenseSummaryAfterWrite
        except IOError as e:
            # Essential Error
            console.print(
                f"\n[bold red]Error:[/bold red] writing to output file '{outputPath}': {e}"
            )
            return 1

        # --- Display License Summary ---
        DisplayLicenseSummaryAfterWrite(
            requestedLicenseIdLower,
            licensesData,
            missingArgsFlag,  # Pass flag indicating if any CLI args were missing for found placeholders
            unfilledPlaceholdersForWarning,  # Pass the set of placeholders that lacked direct CLI args
            outputPath,
        )

        return 0

    # If no action argument was given
    # Essential Error/Help
    console.print(
        "\n[bold red]Error:[/bold red] No action specified. Select one of: --list, --detailed-list, --info, --show-placeholders, --compare, --find, --license."
    )
    parser.print_help(file=sys.stderr)  # Print help to stderr
    return 1


if __name__ == "__main__":
    sys.exit(main())
