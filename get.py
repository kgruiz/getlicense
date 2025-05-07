# -*- coding: utf-8 -*-
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
    from rich.text import Text
except ImportError:
    # Fallback print if rich is not installed
    print(
        "Error: 'rich' library not found. Please install it: pip install rich",
        file=sys.stderr,
    )

    # Define a dummy Console and Progress for basic functionality
    class DummyConsole:
        def print(self, *args, **kwargs):
            # Simple print, ignoring style etc.
            print(*args)

    class DummyProgress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def add_task(self, description, total):
            return 0  # Dummy task ID

        def update(self, task_id, description):
            pass

        def advance(self, task_id):
            pass

    Console = DummyConsole
    Progress = DummyProgress
    Text = str  # Fallback Text to simple string

# --- Constants ---
GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"
DATA_PATH: str = "_data"
CACHE_FILENAME: str = "license_cache.json"

# Map standard placeholders to command-line arguments
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

# --- Global Variables ---
# Instantiate Console for rich output
console = Console()
# Global flag to store verbose state, set in main()
_VERBOSE = False


def VerbosePrint(*args, **kwargs):
    """
    Prints only if the verbose flag is set.

    Outputs to stderr to separate status messages from potential stdout data.

    Parameters
    ----------
    *args : tuple
        Arguments to pass to the print function.
    **kwargs : dict
        Keyword arguments to pass to the print function.
    """
    if _VERBOSE:
        # Use standard print to stderr for verbose messages
        print(*args, file=sys.stderr, **kwargs)


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
    if githubToken:
        headers["Authorization"] = f"token {githubToken}"

    url: str = f"{GITHUB_API_URL}{endpoint}"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        console.print(
            f"[bold red]Error:[/bold red] Timeout while fetching from GitHub API ({url})"
        )
        return None
    except requests.exceptions.RequestException as e:
        console.print(
            f"[bold red]Error:[/bold red] fetching from GitHub API ({url}): {e}"
        )
        if hasattr(e, "response") and e.response is not None:
            console.print(f"Response Status: {e.response.status_code}")
            # Use rich Text to prevent accidental markup interpretation in response body
            console.print(f"Response Body: {Text(e.response.text[:500])}...")
            if e.response.status_code == 403:
                rateLimitInfo = e.response.headers.get("X-RateLimit-Remaining", "N/A")
                console.print(
                    f"[yellow]Hint:[/yellow] Check GitHub API rate limits (Remaining: {rateLimitInfo}) or authentication (set GITHUB_TOKEN)."
                )
        return None
    except Exception as e:
        console.print(
            f"[bold red]An unexpected error occurred during API call:[/bold red] {e}"
        )
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
    VerbosePrint(f"Fetching current file list from GitHub ({repoPath})...")
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{repoPath}?ref={BRANCH}"
    data = GetGithubApi(endpoint)
    if not data or not isinstance(data, list):
        # Error printed by GetGithubApi
        return None
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
        response = requests.get(downloadUrl, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.Timeout:
        console.print(
            f"\n[bold red]Error:[/bold red] Timeout fetching content from {downloadUrl}"
        )
        return None
    except requests.exceptions.RequestException as e:
        console.print(
            f"\n[bold red]Error:[/bold red] fetching content from {downloadUrl}: {e}"
        )
        return None
    except Exception as e:
        console.print(
            f"\n[bold red]An unexpected error occurred fetching content from {downloadUrl}:[/bold red] {e}"
        )
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

    if fileContent.strip().startswith("---"):
        parts = fileContent.split("---", 2)
        if len(parts) >= 3:
            frontMatterRaw: str = parts[1].strip()
            body = parts[2].strip()
            try:
                frontMatter = yaml.safe_load(frontMatterRaw) or {}
                if not isinstance(frontMatter, dict):
                    VerbosePrint(
                        f"Warning: Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}
                spdxId = frontMatter.get("spdx-id")
            except yaml.YAMLError as e:
                VerbosePrint(
                    f"Warning: YAML parse error for {filename}: {e}. Fallback."
                )
                frontMatter = {}
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )
                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()
        else:
            VerbosePrint(f"Warning: Malformed front matter in {filename}.")
            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)
    else:
        VerbosePrint(f"Warning: No front matter '---' in {filename}.")
        spdxId = GuessSpdxFromFilename(filename)

    if not spdxId and "spdx-id" in frontMatter:
        spdxId = frontMatter["spdx-id"]
    if not spdxId:
        console.print(
            f"[bold red]Error:[/bold red] Could not determine SPDX ID for {filename}. Skipping."
        )
        return None

    frontMatter.setdefault("spdx-id", spdxId)
    frontMatter.setdefault("title", spdxId)
    frontMatter.setdefault("nickname", None)
    frontMatter.setdefault("description", None)
    frontMatter.setdefault("permissions", [])
    frontMatter.setdefault("conditions", [])
    frontMatter.setdefault("limitations", [])

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
    if re.match(r"^[A-Za-z0-9.\-\+]+$", spdxIdGuess):
        return spdxIdGuess
    else:
        VerbosePrint(
            f"Warning: Filename {filename} doesn't look like a typical SPDX ID format. Cannot reliably guess."
        )
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
        return data
    except yaml.YAMLError as e:
        console.print(
            f"[bold red]Error:[/bold red] parsing YAML data file {filename}: {e}"
        )
        return None
    except Exception as e:
        console.print(
            f"[bold red]An unexpected error occurred parsing data file {filename}:[/bold red] {e}"
        )
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
    if cacheFilePath.exists():
        try:
            with open(cacheFilePath, "r", encoding="utf-8") as f:
                content = f.read()
                if not content:
                    VerbosePrint(
                        f"Warning: Cache file {cacheFilePath} is empty. Starting fresh."
                    )
                    return {}
                return json.loads(content)
        except (IOError, json.JSONDecodeError) as e:
            VerbosePrint(
                f"Warning: Could not load or parse cache file {cacheFilePath}: {e}. Starting fresh."
            )
            return {}
        except Exception as e:
            VerbosePrint(f"An unexpected error occurred loading cache: {e}")
            return {}
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
        cacheFilePath.parent.mkdir(parents=True, exist_ok=True)
        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
        VerbosePrint(f"Cache saved to {cacheFilePath}")
    except IOError as e:
        console.print(
            f"[bold red]Error:[/bold red] Could not save cache file {cacheFilePath}: {e}"
        )
    except Exception as e:
        console.print(
            f"[bold red]An unexpected error occurred saving cache:[/bold red] {e}"
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
    githubDataFiles = FetchGithubDirListing(DATA_PATH)
    if githubDataFiles is None:
        VerbosePrint(
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
                processedDataFiles[cacheKey] = cachedEntry

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
    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)
    processedLicenses = {}
    if githubLicenseFiles is None:
        VerbosePrint(
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
            cachedSpdxLower = None
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
                spdxIdInCache = cachedEntry.get("spdx_id")
                if spdxIdInCache and spdxIdInCache.lower() != cachedSpdxLower:
                    VerbosePrint(f"  SPDX ID mismatch for cached {name}. Updating key.")
                    processedLicenses[spdxIdInCache.lower()] = cachedEntry
                    needsSave = True
                else:
                    processedLicenses[cachedSpdxLower] = cachedEntry

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

    if totalFilesToFetch > 0:
        console.print()  # Add newline before progress bar
        with Progress(
            *progressColumns, console=console, transient=False
        ) as progress:  # Use main console, make bar persistent
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
                if content:
                    parsedData = ParseDataFile(filename, content)
                    if parsedData is not None:
                        processedDataFiles[f"data:{filename}"] = {
                            "sha": ghInfo.get("sha", ""),
                            "content": parsedData,
                        }
                    elif f"data:{filename}" in cachedData:
                        VerbosePrint(
                            f"  Failed to parse {filename}, keeping old cached version."
                        )
                        processedDataFiles[f"data:{filename}"] = cachedData[
                            f"data:{filename}"
                        ]
                else:
                    VerbosePrint(f"  Failed to fetch data file {filename}.")
                progress.advance(fetchTask)

            # Fetch license files
            for ghInfo in licenseFilesToFetch:
                filename = ghInfo.get("name", "unknown.txt")
                progress.update(
                    fetchTask, description=f"[cyan]Fetching license: {filename}"
                )
                content = FetchFileContent(ghInfo.get("download_url"))
                if content:
                    parsedData = ParseLicenseFile(filename, content)
                    if parsedData:
                        spdxLower = parsedData["spdx_id"].lower()
                        fm = parsedData["front_matter"]
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
                            "file_content_cached": content,
                        }
                    elif filename in {
                        v.get("filename")
                        for k, v in cachedData.items()
                        if not k.startswith("data:")
                    }:
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

    if needsSave or deletedDataFilenames or deletedLicenseFilenames or forceRefresh:
        SaveCache(cacheFilePath, finalCacheData)  # SaveCache prints verbose message
    else:
        VerbosePrint("Cache is up-to-date.")

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
    for placeholder, value in replacements.items():
        phFormatted: str = f"[{placeholder.strip('[]')}]"
        filledText = filledText.replace(phFormatted, str(value))
    return filledText


def ListLicenses(licensesData: dict[str, object]) -> None:
    """
    Prints a simple list of available licenses from cached data using rich.

    Parameters
    ----------
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    console.print("\n[bold]Available Licenses (SPDX ID: Title):[/bold]")
    console.print("-" * 50)
    sortedKeys: list[str] = sorted(licenseKeys)
    for spdxLower in sortedKeys:
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        console.print(f"  [cyan]{spdx:<25}[/cyan] : {title}")
    console.print("-" * 50)


def PrintDetailedList(licensesData: dict[str, object]) -> None:
    """
    Prints a detailed list of licenses using cached basic info and rule labels with rich formatting.

    Parameters
    ----------
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    rulesMap = {}
    if not rulesData:
        VerbosePrint(
            "Warning: rules.yml data not found in cache. Rule labels may be missing."
        )
    else:
        for category in ["permissions", "conditions", "limitations"]:
            rulesMap[category] = {
                rule.get("tag"): rule
                for rule in rulesData.get(category, [])
                if isinstance(rule, dict) and rule.get("tag")
            }

    console.print("\n[bold]--- Detailed License List (from cache) ---[/bold]")
    sortedKeys: list[str] = sorted(licenseKeys)
    for i, spdxLower in enumerate(sortedKeys):
        data = licensesData[spdxLower]
        spdx: str = data.get("spdx_id", "N/A")
        title: str = data.get("title", "N/A")
        nickname: str | None = data.get("nickname")
        description: str = data.get("description", "No description available.")
        perms_tags = data.get("permissions", [])
        conds_tags = data.get("conditions", [])
        lims_tags = data.get("limitations", [])

        console.print(f"\n[bold cyan]SPDX ID:[/bold cyan] {spdx}")
        console.print(f"[bold]Title:[/bold] {title}")
        if nickname:
            console.print(f"[italic]Nickname:[/italic] {nickname}")

        truncated_desc = textwrap.shorten(
            description or "", width=100, placeholder="..."
        )
        console.print(f"[bold]Description:[/bold] {truncated_desc}")

        def GetRuleLabels(tags: list[str], category: str) -> list[str]:
            labels = []
            catRulesMap = rulesMap.get(category, {})
            for tag in tags:
                ruleInfo = catRulesMap.get(tag)
                labels.append(ruleInfo.get("label", tag) if ruleInfo else tag)
            return sorted(labels)

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

        if i < len(sortedKeys) - 1:
            console.print("[dim]---[/dim]")

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
        console.print(
            f"[bold red]Error:[/bold red] Basic info for {spdxIdLower.upper()} not found in cache."
        )
        return None

    content = basicInfo.get("file_content_cached")
    fullLicenseData = None

    if content:
        VerbosePrint(
            f"Using cached content for {basicInfo.get('filename', spdxIdLower.upper())}."
        )
        fullLicenseData = ParseLicenseFile(
            basicInfo.get("filename", "unknown"), content
        )
        if not fullLicenseData:
            VerbosePrint(
                f"Warning: Failed to parse cached content for {basicInfo.get('filename', spdxIdLower.upper())}. Re-fetching."
            )
            content = None  # Force re-fetch

    if not content:  # Need to fetch
        filename = basicInfo.get("filename")
        if not filename:
            console.print(
                f"[bold red]Error:[/bold red] Filename missing for {spdxIdLower.upper()} in cache. Cannot fetch full info."
            )
            return None

        VerbosePrint(f"Fetching full content for {filename} from GitHub...")
        githubFiles = FetchGithubDirListing(LICENSES_PATH)
        downloadUrl = None
        if githubFiles:
            for item in githubFiles:
                if isinstance(item, dict) and item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break
        if not downloadUrl:
            console.print(
                f"[bold red]Error:[/bold red] Could not find download URL for {filename}. Cannot fetch content."
            )
            return None

        content = FetchFileContent(downloadUrl)
        if content:
            fullLicenseData = ParseLicenseFile(filename, content)
        else:
            # FetchFileContent prints essential error
            return None

    if not fullLicenseData:
        # ParseLicenseFile might print essential error
        console.print(
            f"[bold red]Error:[/bold red] Failed to get full license data for {spdxIdLower.upper()}."
        )
        return None

    return fullLicenseData


def DisplayLicenseInfo(spdxIdLower: str, licensesData: dict[str, object]) -> None:
    """
    Prints the formatted metadata for a license using GetFullLicenseData and rich.

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license to display.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)
    if not fullLicenseData:
        return

    fm: dict[str, object] = fullLicenseData.get("front_matter", {})
    spdxId: str = fullLicenseData.get("spdx_id", "N/A")
    title: str = fm.get("title", "N/A")
    body = fullLicenseData.get("body", "")

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    console.print(f"\n[bold]--- License Information: {title} ({spdxId}) ---[/bold]")

    if fm.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {fm['nickname']}")

    def PrintTextBlock(label: str, text: str | None) -> None:
        if text:
            console.print(f"\n[bold]{label}:[/bold]")
            # Use Text object to prevent markup interpretation in the description itself
            console.print(
                Text(
                    textwrap.fill(
                        text, width=78, initial_indent="  ", subsequent_indent="  "
                    )
                )
            )

    PrintTextBlock("Description", fm.get("description"))
    PrintTextBlock("How to Apply", fm.get("how"))

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

    if fm.get("using") and isinstance(fm["using"], dict):
        console.print("\n[bold]Notable Projects Using This License:[/bold]")
        for project, url in fm["using"].items():
            console.print(f"  - {project}: {url}")

    PrintTextBlock("Note", fm.get("note"))

    placeholders = FindPlaceholders(body)
    if placeholders:
        console.print("\n[bold]Placeholders in Body:[/bold]")
        for ph in sorted(list(placeholders)):
            ph_lower = ph.lower()
            fieldInfo = fieldsData.get(ph_lower)
            description = (
                fieldInfo.get("description", "[dim]No description available[/dim]")
                if fieldInfo
                else "[dim]No description available[/dim]"
            )
            argSuggestion = PLACEHOLDER_TO_ARG_MAP.get(
                ph_lower, f"[dim](no direct argument for '[{ph}]')[/dim]"
            )
            defaultInfo = ""
            if ph_lower in ["year", "yyyy"]:
                defaultInfo = " [dim](defaults to current year)[/dim]"
            console.print(f"  - [bold magenta][{ph}][/bold magenta]")
            console.print(f"    [italic]Description:[/italic] {description}")
            console.print(
                f"    [italic]Argument:[/italic] {argSuggestion}{defaultInfo}"
            )
    else:
        console.print("\n[bold]Placeholders in Body:[/bold] ([dim]None detected[/dim])")

    console.print("\n[bold]--- End License Information ---[/bold]")


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """
    Compares licenses based on rules using rich formatting.

    Parameters
    ----------
    spdxIdsLower : list[str]
        A list of lowercase SPDX IDs for the licenses to compare.
    licensesData : dict[str, object]
        The dictionary containing cached license and data file information.
    """
    if len(spdxIdsLower) < 2:
        console.print(
            "\n[bold red]Error:[/bold red] Need at least two license SPDX IDs to compare."
        )
        return

    licensesToCompare = []
    for spdxLower in spdxIdsLower:
        fullLicenseData = GetFullLicenseData(spdxLower, licensesData)
        if fullLicenseData:
            licensesToCompare.append(fullLicenseData)
        else:
            VerbosePrint(
                f"Could not get data for {spdxLower.upper()}, skipping comparison."
            )

    if len(licensesToCompare) < 2:
        console.print(
            "\n[bold red]Error:[/bold red] Cannot perform comparison: Need at least two valid licenses."
        )
        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        console.print(
            "[bold red]Error:[/bold red] rules.yml data not found in cache. Cannot compare rules."
        )
        return

    rulesMap = {}
    for category in ["permissions", "conditions", "limitations"]:
        rulesMap[category] = {
            rule.get("tag"): rule
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        }

    console.print("\n[bold]--- Comparing Licenses ---[/bold]")
    licenseSpdxIds = [lic["spdx_id"] for lic in licensesToCompare]
    console.print(f"[bold]Comparing:[/bold] {', '.join(licenseSpdxIds)}")

    allRuleTags = set()
    for lic in licensesToCompare:
        fm = lic.get("front_matter", {})
        for cat in ["permissions", "conditions", "limitations"]:
            allRuleTags.update(fm.get(cat, []))

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
        maxLabelWidth = max(maxLabelWidth, 25)

    categoryColors = {
        "permissions": "green",
        "conditions": "yellow",
        "limitations": "red",
    }

    for category in ["permissions", "conditions", "limitations"]:
        ruleTags = tagsByCategory[category]
        if not ruleTags:
            continue

        color = categoryColors.get(category, "white")
        console.print(f"\n[bold {color}]{category.capitalize()}:[/bold {color}]")
        for tag in ruleTags:
            ruleInfo = rulesMap.get(category, {}).get(tag)
            label = ruleInfo.get("label", tag) if ruleInfo else tag

            line = f"  {label:<{maxLabelWidth}} : "
            indicators = []
            for lic in licensesToCompare:
                fm = lic.get("front_matter", {})
                hasRule = tag in fm.get(category, [])
                indicator_symbol = (
                    "[bold green]✓[/bold green]"
                    if hasRule
                    else "[bold red]X[/bold red]"
                )
                indicators.append(f"[dim]{lic['spdx_id']}:[/dim] {indicator_symbol}")
            line += " | ".join(indicators)
            console.print(line)

    console.print("\n[bold]--- End Comparison ---[/bold]")


def FindLicenses(
    requireTags: list[str] | None,
    disallowTags: list[str] | None,
    licensesData: dict[str, object],
) -> None:
    """
    Finds licenses matching require/disallow criteria using cached data and rich.

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

    if not requireTags and not disallowTags:
        console.print(
            "\n[bold red]Error:[/bold red] Please provide at least one --require or --disallow tag for finding licenses."
        )
        return

    licenseKeys = [k for k in licensesData.keys() if not k.startswith("data:")]
    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.[/yellow]")
        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    if not rulesData:
        console.print(
            "[bold red]Error:[/bold red] rules.yml data not found in cache. Cannot validate find tags."
        )
        return

    allValidTags = set()
    for category in ["permissions", "conditions", "limitations"]:
        allValidTags.update(
            rule.get("tag")
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        )

    invalidRequire = [tag for tag in requireTags if tag not in allValidTags]
    invalidDisallow = [tag for tag in disallowTags if tag not in allValidTags]

    if invalidRequire or invalidDisallow:
        console.print("\n[bold red]Error: Invalid rule tags provided:[/bold red]")
        if invalidRequire:
            console.print(f"  Invalid --require tags: {', '.join(invalidRequire)}")
        if invalidDisallow:
            console.print(f"  Invalid --disallow tags: {', '.join(invalidDisallow)}")
        return

    console.print("\n[bold]--- Finding Licenses Matching Criteria ---[/bold]")
    console.print(
        f"[bold]Require:[/bold] {', '.join(requireTags) if requireTags else '[dim]None[/dim]'}"
    )
    console.print(
        f"[bold]Disallow:[/bold] {', '.join(disallowTags) if disallowTags else '[dim]None[/dim]'}"
    )
    console.print("-" * 50)

    matches = []
    for spdxLower in licenseKeys:
        data = licensesData[spdxLower]
        licenseRules = set(
            data.get("permissions", [])
            + data.get("conditions", [])
            + data.get("limitations", [])
        )
        meetsRequire = all(tag in licenseRules for tag in requireTags)
        meetsDisallow = not any(tag in licenseRules for tag in disallowTags)

        if meetsRequire and meetsDisallow:
            matches.append(data)

    if not matches:
        console.print("[yellow]No licenses found matching all criteria.[/yellow]")
    else:
        console.print(f"Found {len(matches)} matching license(s):")
        for matchData in sorted(matches, key=lambda x: x.get("spdx_id", "")):
            console.print(
                f"  - [cyan]{matchData.get('spdx_id', 'N/A')}[/cyan] ({matchData.get('title', 'N/A')})"
            )

    console.print("-" * 50)


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
  %(prog)s --license MIT -f "Jane Doe"      Fill license (uses cache, fetches full content if needed).
  %(prog)s -l Apache-2.0 -f ACME -o LIC     Fill license, output to file LIC (uses cache, fetches content if needed).
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
        "-o", "--output", help="Output file path. Defaults to printing to stdout."
    )

    args = parser.parse_args()
    cacheFilePath = args.cache_file  # Already a Path object

    # Set global verbose flag
    global _VERBOSE
    _VERBOSE = args.verbose

    # Update cache if needed, then load license and data files
    licensesData = UpdateAndLoadLicenseCache(cacheFilePath, args.refresh)
    if not licensesData:
        # Error printed in function
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
        if not licensesData.get(requestedIdLower):
            console.print(
                f"\n[bold red]Error:[/bold red] License '{args.info}' not found in cache."
            )
            console.print(
                "Use --list to see available licenses or --refresh to update cache."
            )
            return 1
        DisplayLicenseInfo(requestedIdLower, licensesData)
        return 0

    if args.show_placeholders:
        requestedIdLower = args.show_placeholders.lower()
        fullLicenseData = GetFullLicenseData(requestedIdLower, licensesData)
        if not fullLicenseData:
            return 1

        basicInfo = licensesData.get(requestedIdLower, {})
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        if not fieldsDataList:
            VerbosePrint(
                "Warning: fields.yml data not found in cache. Placeholder descriptions unavailable."
            )
            fieldsData = {}
        else:
            fieldsData = {
                item["name"].lower(): item
                for item in fieldsDataList
                if isinstance(item, dict) and item.get("name")
            }

        console.print(
            f"\n[bold]Placeholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}):[/bold]"
        )
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))
        if not placeholders:
            console.print("  ([dim]No standard [placeholder] patterns found[/dim])")
        else:
            for ph in sorted(list(placeholders)):
                ph_lower = ph.lower()
                fieldInfo = fieldsData.get(ph_lower)
                description = (
                    fieldInfo.get("description", "[dim]No description available[/dim]")
                    if fieldInfo
                    else "[dim]No description available[/dim]"
                )
                argSuggestion = PLACEHOLDER_TO_ARG_MAP.get(
                    ph_lower, f"[dim](no direct argument for '[{ph}]')[/dim]"
                )
                defaultInfo = ""
                if ph_lower in ["year", "yyyy"]:
                    defaultInfo = (
                        " [dim](defaults to current year if not provided)[/dim]"
                    )
                console.print(f"  - [bold magenta][{ph}][/bold magenta]")
                console.print(f"    [italic]Description:[/italic] {description}")
                console.print(
                    f"    [italic]Argument:[/italic] {argSuggestion}{defaultInfo}"
                )
        return 0

    if args.compare:
        spdxIdsToCompare = [id.lower() for id in args.compare]
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

        CompareLicenses(spdxIdsToCompare, licensesData)
        return 0

    if args.license:
        requestedLicenseIdLower: str = args.license.lower()
        fullLicenseData = GetFullLicenseData(requestedLicenseIdLower, licensesData)
        if not fullLicenseData:
            return 1

        title: str = fullLicenseData.get("front_matter", {}).get("title", "N/A")
        spdxId: str = fullLicenseData.get("spdx_id", "N/A")
        body: str = fullLicenseData.get("body", "")

        console.print(f"\n[bold]Using license:[/bold] {title} ({spdxId})")

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
            VerbosePrint(
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
        VerbosePrint("Checking required placeholders:")
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
                VerbosePrint(
                    f"  Warning: Placeholder [{ph}] ({description}) found, but no value provided via {argSuggestion}."
                )
                missingArgs = True
        if missingArgs:
            VerbosePrint("  License generated, but placeholders might remain unfilled.")

        # --- Fill and Output ---
        filledLicense: str = FillLicenseTemplate(body, replacements)
        if args.output:
            try:
                outputPath: Path = Path(args.output)
                with open(outputPath, "w", encoding="utf-8") as f:
                    f.write(filledLicense + "\n")  # Add trailing newline
                console.print(f"\nLicense successfully written to '{args.output}'")
            except IOError as e:
                console.print(
                    f"\n[bold red]Error:[/bold red] writing to output file '{args.output}': {e}"
                )
                return 1
        else:
            # Print filled license text to stdout without extra formatting
            print("\n--- Filled License Text ---")
            print(filledLicense)
            print("--- End License Text ---\n")
        return 0

    # If no action argument was given
    console.print(
        "\n[bold red]Error:[/bold red] No action specified. Select one of: --list, --detailed-list, --info, --show-placeholders, --compare, --find, --license."
    )
    parser.print_help()  # Print help to stdout in this case
    return 1


if __name__ == "__main__":
    sys.exit(main())
