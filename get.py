import argparse
import base64
import json
import os
import re
import sys
import textwrap
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import requests
import yaml

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
    from rich.table import Table
except ImportError:
    print(
        "Error: 'rich' library not found. Please install it: pip install rich",
        file=sys.stderr,
    )

    class DummyConsole:
        def __init__(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
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
            return 0

        def update(self, *args, **kwargs):
            pass

        def advance(self, *args, **kwargs):
            pass

    class DummyTable:
        def __init__(self, *args, **kwargs):
            pass

        def add_column(self, *args, **kwargs):
            pass

        def add_row(self, *args, **kwargs):
            pass

        def add_section(self, *args, **kwargs):
            pass

    Console = DummyConsole
    Progress = DummyProgress
    Table = DummyTable


GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"
DATA_PATH: str = "_data"
CACHE_FILENAME: str = "license_cache.json"
USER_PLACEHOLDERS_CACHE_KEY: str = "user_placeholders"

# 'year' is intentionally excluded as it's not cached with user preferences.
CACHABLE_PLACEHOLDER_KEYS: list[str] = [
    "fullname",
    "project",
    "email",
    "projecturl",
]

# Maps CLI argument names (from argparse, e.g., args.fullname) to standardized cache keys
CLI_ARG_TO_CACHE_KEY: dict[str, str] = {
    "fullname": "fullname",
    "project": "project",
    "email": "email",
    "projecturl": "projecturl",
}

# Maps raw placeholder strings (found in license templates, keys are lowercased for matching)
# to standardized internal keys.
RAW_PLACEHOLDER_TO_STANDARD_KEY: dict[str, str] = {
    "fullname": "fullname",
    "name of copyright owner": "fullname",
    "login": "fullname",  # Assuming login often refers to the user's full name or org
    "project": "project",
    "email": "email",
    "projecturl": "projecturl",
    "year": "year",  # 'year' is a standard key, but not for CACHABLE_PLACEHOLDER_KEYS
    "yyyy": "year",
    "description": "description",  # 'description' is standard, but not for CACHABLE_PLACEHOLDER_KEYS
}


PLACEHOLDER_TO_ARG_MAP: dict[str, str] = {
    "fullname": "--fullname",
    "login": "--fullname (recommended for user/org name)",
    "email": "--email",
    "project": "--project",
    "description": "(no direct argument for '[description]')",
    "year": "--year",
    "projecturl": "--projecturl",
    "yyyy": "--year",
    "name of copyright owner": "--fullname",
}


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
        ("Patent use (Perm)", "patent-use_perm"),
        ("Patent use (Lim)", "patent-use_lim"),
    ]
)


console = Console(stderr=True, highlight=False)
stdout_console = Console(highlight=False)


isVerbose = False
# Tracks if an action modified the cache (e.g., placeholder commands)
isCacheModifiedByAction = False


def VerbosePrint(*args, **kwargs) -> None:
    """
    Prints messages to the console if verbose mode is enabled.

    Parameters
    ----------
    *args
        Variable length argument list to print.
    **kwargs
        Arbitrary keyword arguments for the print function.
    """

    if isVerbose:

        console.print(*args, **kwargs)


def GetGithubApi(endpoint: str) -> dict | list | None:
    """
    Fetches data from the GitHub API for a given endpoint.

    Parameters
    ----------
    endpoint : str
        The API endpoint to query (e.g., /repos/owner/repo/contents/path).

    Returns
    -------
    dict | list | None
        The JSON response from the API as a dictionary or list, or None on error.
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
            f"[bold red]Error:[/bold red] Timeout fetching from GitHub API ({url})"
        )

        return None

    except requests.exceptions.RequestException as e:
        console.print(
            f"[bold red]Error:[/bold red] Fetching from GitHub API ({url}): {e}"
        )

        if hasattr(e, "response") and e.response is not None:
            console.print(f"Response Status: {e.response.status_code}")
            console.print(f"Response Body: {e.response.text[:500]}...")

            if e.response.status_code == 403:
                rateLimitInfo = e.response.headers.get("X-RateLimit-Remaining", "N/A")
                console.print(
                    f"[yellow]Hint:[/yellow] Check GitHub API rate limits (Remaining: {rateLimitInfo}) or GITHUB_TOKEN."
                )

        return None

    except Exception as e:
        console.print(
            f"[bold red]Error:[/bold red] Unexpected error during API call: {e}"
        )

        return None


def FetchGithubDirListing(repoPath: str) -> list[dict[str, object]] | None:
    """
    Fetches the directory listing from a GitHub repository path.

    Parameters
    ----------
    repoPath : str
        The path within the repository (e.g., _licenses).

    Returns
    -------
    list[dict[str, object]] | None
        A list of file/directory information dictionaries, or None on error.
    """

    VerbosePrint(f"Fetching current file list from GitHub ({repoPath})...")
    endpoint: str = f"/repos/{OWNER}/{REPO}/contents/{repoPath}?ref={BRANCH}"
    data = GetGithubApi(endpoint)

    if not data or not isinstance(data, list):
        console.print(
            f"[bold red]Error:[/bold red] Could not fetch or parse directory listing for {repoPath}"
        )

        return None

    return data


def FetchFileContent(downloadUrl: str) -> str | None:
    """
    Fetches the content of a file given its download URL.

    Parameters
    ----------
    downloadUrl : str
        The direct URL to download the file content.

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
            f"\n[bold red]Error:[/bold red] Fetching content from {downloadUrl}: {e}"
        )

        return None

    except Exception as e:
        console.print(
            f"\n[bold red]Error:[/bold red] Unexpected error fetching content from {downloadUrl}: {e}"
        )

        return None


def ParseLicenseFile(filename: str, fileContent: str) -> dict[str, object] | None:
    """
    Parses a license file content for front matter and body.

    Parameters
    ----------
    filename : str
        The name of the license file (for context in messages).
    fileContent : str
        The raw text content of the license file.

    Returns
    -------
    dict[str, object] | None
        A dictionary containing 'spdx_id', 'front_matter', and 'body', or None on error.
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
                        f"[yellow]Warning:[/yellow] Front matter in {filename} not a dictionary. Fallback."
                    )
                    frontMatter = {}
                spdxId = frontMatter.get("spdx-id")

            except yaml.YAMLError as e:
                VerbosePrint(
                    f"[yellow]Warning:[/yellow] YAML parse error for {filename}: {e}. Fallback."
                )
                frontMatter = {}
                matchSpdx = re.search(
                    r"spdx-id:\s*([^\n]+)", frontMatterRaw, re.IGNORECASE
                )

                if matchSpdx:
                    spdxId = matchSpdx.group(1).strip()

        else:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Malformed front matter in {filename}."
            )

            if not spdxId:
                spdxId = GuessSpdxFromFilename(filename)

    else:
        VerbosePrint(f"[yellow]Warning:[/yellow] No front matter '---' in {filename}.")
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
    Guesses the SPDX ID from a filename.

    Parameters
    ----------
    filename : str
        The filename (e.g., 'mit.txt').

    Returns
    -------
    str | None
        The guessed SPDX ID (e.g., 'mit'), or None if it cannot be guessed.
    """

    spdxIdGuess: str = os.path.splitext(filename)[0]

    if re.match(r"^[A-Za-z0-9.\-\+]+$", spdxIdGuess):

        return spdxIdGuess

    else:
        VerbosePrint(
            f"[yellow]Warning:[/yellow] Filename {filename} doesn't look like SPDX ID. Cannot guess."
        )

        return None


def ParseDataFile(filename: str, fileContent: str) -> object | None:
    """
    Parses a YAML data file.

    Parameters
    ----------
    filename : str
        The name of the data file (for context in messages).
    fileContent : str
        The raw YAML content of the data file.

    Returns
    -------
    object | None
        The parsed YAML data (often a list or dict), or None on error.
    """

    try:

        return yaml.safe_load(fileContent)

    except yaml.YAMLError as e:
        console.print(
            f"[bold red]Error:[/bold red] parsing YAML data file {filename}: {e}"
        )

        return None

    except Exception as e:
        console.print(
            f"[bold red]Error:[/bold red] Unexpected error parsing data file {filename}: {e}"
        )

        return None


def LoadCache(cacheFilePath: Path) -> dict[str, object]:
    """
    Loads cache data from a JSON file.

    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.

    Returns
    -------
    dict[str, object]
        The loaded cache data, or an empty dictionary if loading fails or file doesn't exist.
    """

    if cacheFilePath.exists():

        try:

            with open(cacheFilePath, "r", encoding="utf-8") as f:
                content = f.read()

                if not content:
                    VerbosePrint(
                        f"[yellow]Warning:[/yellow] Cache file {cacheFilePath} is empty."
                    )

                    return {}

                return json.loads(content)

        except (IOError, json.JSONDecodeError) as e:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Could not load/parse cache {cacheFilePath}: {e}."
            )

            return {}

        except Exception as e:
            VerbosePrint(
                f"[yellow]Warning:[/yellow] Unexpected error loading cache: {e}"
            )

            return {}

    return {}


def SaveCache(cacheFilePath: Path, cacheData: dict[str, object]) -> None:
    """
    Saves cache data to a JSON file.

    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.
    cacheData : dict[str, object]
        The cache data to save.
    """

    try:

        cacheFilePath.parent.mkdir(parents=True, exist_ok=True)

        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
        VerbosePrint(f"Cache saved to {cacheFilePath}")

    except IOError as e:
        console.print(
            f"[bold red]Error:[/bold red] Could not save cache {cacheFilePath}: {e}"
        )

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Unexpected error saving cache: {e}")


def UpdateAndLoadLicenseCache(
    cacheFilePath: Path, forceRefresh: bool = False
) -> tuple[dict[str, object], bool]:
    """
    Updates the local license cache from GitHub and loads it.

    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.
    forceRefresh : bool, optional
        If True, forces a full refresh of all data from GitHub, by default False.

    Returns
    -------
    tuple[dict[str, object], bool]
        A tuple containing the loaded (and potentially updated) cache data,
        and a boolean indicating if the cache was updated by fetching files from GitHub.
    """

    VerbosePrint("Loading cache...")
    cachedData = LoadCache(cacheFilePath) if not forceRefresh else {}

    if forceRefresh:
        VerbosePrint("Cache refresh forced.")

    # True if any GitHub files were fetched/updated
    cacheUpdatedByFetch = False
    processedDataFiles = {}
    dataFilesToFetch = []
    licenseFilesToFetch = []

    progressColumns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ]

    githubDataFiles = FetchGithubDirListing(DATA_PATH)

    if githubDataFiles is None:
        VerbosePrint(
            "[yellow]Warning:[/yellow] Failed to fetch data file list. Using potentially stale cached data."
        )

        for key, val in cachedData.items():

            if key.startswith("data:") or key == USER_PLACEHOLDERS_CACHE_KEY:
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
            ghSha = ghInfo.get("sha")

            if not ghSha or not (
                cachedEntry
                and isinstance(cachedEntry, dict)
                and cachedEntry.get("sha") == ghSha
            ):
                dataFilesToFetch.append(ghInfo)
                cacheUpdatedByFetch = True
                VerbosePrint(
                    f"  Detected {'change in' if cachedEntry else 'new'} data file {name}."
                )

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
            cacheUpdatedByFetch = True
        # Preserve user_placeholders if it exists

        if USER_PLACEHOLDERS_CACHE_KEY in cachedData:
            processedDataFiles[USER_PLACEHOLDERS_CACHE_KEY] = cachedData[
                USER_PLACEHOLDERS_CACHE_KEY
            ]

    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)
    processedLicenses = {}

    if githubLicenseFiles is None:
        VerbosePrint(
            "[yellow]Warning:[/yellow] Failed to fetch license file list. Using potentially stale cached licenses."
        )

        for key, val in cachedData.items():

            if not key.startswith("data:") and key != USER_PLACEHOLDERS_CACHE_KEY:
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
                    and key != USER_PLACEHOLDERS_CACHE_KEY
                    and isinstance(val, dict)
                    and val.get("filename") == name
                ):
                    cachedEntry = val
                    cachedSpdxLower = key
                    break
            ghSha = ghInfo.get("sha")

            if not ghSha or not (cachedEntry and cachedEntry.get("sha") == ghSha):
                licenseFilesToFetch.append(ghInfo)
                cacheUpdatedByFetch = True
                VerbosePrint(
                    f"  Detected {'change in' if cachedEntry else 'new'} file {name}."
                )

            else:
                spdxIdInCache = cachedEntry.get("spdx_id")

                if spdxIdInCache and spdxIdInCache.lower() != cachedSpdxLower:
                    VerbosePrint(f"  SPDX ID mismatch for cached {name}. Updating key.")
                    processedLicenses[spdxIdInCache.lower()] = cachedEntry
                    cacheUpdatedByFetch = True

                else:
                    processedLicenses[cachedSpdxLower] = cachedEntry
        cachedLicenseFilenames = {
            entry.get("filename")
            for key, entry in cachedData.items()
            if not key.startswith("data:")
            and key != USER_PLACEHOLDERS_CACHE_KEY
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
            cacheUpdatedByFetch = True

    totalFilesToFetch = len(dataFilesToFetch) + len(licenseFilesToFetch)

    if totalFilesToFetch > 0:
        console.print()

        with Progress(*progressColumns, console=console, transient=False) as progress:
            fetchTask = progress.add_task(
                "[cyan]Syncing cache...", total=totalFilesToFetch
            )

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
                        and k != USER_PLACEHOLDERS_CACHE_KEY
                    }:
                        VerbosePrint(
                            f"  Failed to parse {filename}, keeping old cached version."
                        )

                        for key, val in cachedData.items():

                            if (
                                not key.startswith("data:")
                                and key != USER_PLACEHOLDERS_CACHE_KEY
                                and isinstance(val, dict)
                                and val.get("filename") == filename
                            ):
                                processedLicenses[key] = val
                                break

                else:
                    VerbosePrint(f"  Failed to fetch content for {filename}.")
                progress.advance(fetchTask)

        console.print()

    keysToRemoveFromProcessed = [
        key
        for key, val in processedLicenses.items()
        if not key.startswith("data:")
        and key != USER_PLACEHOLDERS_CACHE_KEY
        and val.get("filename") in deletedLicenseFilenames
    ]

    for key in keysToRemoveFromProcessed:
        processedLicenses.pop(key, None)

    finalCacheData = {**processedDataFiles, **processedLicenses}
    # Ensure it's carried over if it existed

    if (
        USER_PLACEHOLDERS_CACHE_KEY in cachedData
        and USER_PLACEHOLDERS_CACHE_KEY not in finalCacheData
    ):
        finalCacheData[USER_PLACEHOLDERS_CACHE_KEY] = cachedData[
            USER_PLACEHOLDERS_CACHE_KEY
        ]

    if cacheUpdatedByFetch or forceRefresh:
        # The decision to save is now primarily in main()
        pass

    else:
        VerbosePrint("Cache is up-to-date regarding remote files.")

    return finalCacheData, cacheUpdatedByFetch


def FindPlaceholders(templateBody: str) -> set[str]:
    """
    Finds all unique placeholders (e.g., [year]) in a template string.

    Parameters
    ----------
    templateBody : str
        The template string to search.

    Returns
    -------
    set[str]
        A set of unique placeholder names found (without brackets).
    """

    return set(re.findall(r"\[([^\]]+)\]", templateBody))


def FillLicenseTemplate(templateBody: str, replacements: dict[str, str]) -> str:
    """
    Fills placeholders in a license template string with provided values.

    Parameters
    ----------
    templateBody : str
        The license template string with placeholders.
    replacements : dict[str, str]
        A dictionary where keys are standardized placeholder names (e.g., 'year', 'fullname')
        and values are the strings to replace them with.

    Returns
    -------
    str
        The license template with placeholders filled.
    """

    filledText: str = templateBody
    # Standardize common variations for replacement
    # e.g. [year] and [yyyy] should both be replaced if 'year' is in replacements

    for placeholderRaw, value in replacements.items():
        # Find placeholders in the *current* state of filledText

        for phVariantInBodyRawCase in FindPlaceholders(filledText):
            phVariantInBodyLower = phVariantInBodyRawCase.lower()
            standardKeyForBodyPh = RAW_PLACEHOLDER_TO_STANDARD_KEY.get(
                phVariantInBodyLower
            )

            # placeholderRaw is already a standard key from replacements
            if standardKeyForBodyPh and standardKeyForBodyPh == placeholderRaw.lower():
                filledText = filledText.replace(
                    f"[{phVariantInBodyRawCase}]", str(value)
                )

    return filledText


def ListLicenses(licensesData: dict[str, object], targetLicenseKeys: list[str]) -> None:
    """
    Lists available licenses with their SPDX ID and Title.

    Parameters
    ----------
    licensesData : dict[str, object]
        The cache data containing all license information.
    targetLicenseKeys : list[str]
        A list of lowercase SPDX IDs of licenses to list.
    """

    if not targetLicenseKeys:
        console.print("[yellow]No licenses found or specified.[/yellow]")

        return

    console.print("\n[bold]Available Licenses (SPDX ID: Title):[/bold]")
    console.print("[dim]" + ("-" * 50) + "[/dim]")
    sortedKeys: list[str] = sorted(targetLicenseKeys)

    for spdxLower in sortedKeys:
        data = licensesData.get(spdxLower)

        if not data or not isinstance(data, dict):
            VerbosePrint(f"Skipping invalid key: {spdxLower}")
            continue
        console.print(
            f"  [cyan]{data.get('spdx_id', 'N/A'):<25}[/cyan] : {data.get('title', 'N/A')}"
        )


def PrintDetailedList(
    licensesData: dict[str, object], targetLicenseKeys: list[str]
) -> None:
    """
    Prints a detailed list of specified licenses including rules.

    Parameters
    ----------
    licensesData : dict[str, object]
        The cache data containing all license and rules information.
    targetLicenseKeys : list[str]
        A list of lowercase SPDX IDs of licenses to detail.
    """

    if not targetLicenseKeys:
        console.print("[yellow]No licenses found or specified.[/yellow]")

        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    rulesMap = {}

    if not rulesData:
        VerbosePrint(
            "[yellow]Warning:[/yellow] rules.yml not in cache. Rule labels may be missing."
        )

    else:

        for category in ["permissions", "conditions", "limitations"]:
            rulesMap[category] = {
                rule.get("tag"): rule
                for rule in rulesData.get(category, [])
                if isinstance(rule, dict) and rule.get("tag")
            }

    sortedKeys: list[str] = sorted(targetLicenseKeys)

    for i, spdxLower in enumerate(sortedKeys):
        data = licensesData.get(spdxLower)

        if not data or not isinstance(data, dict):
            VerbosePrint(f"Skipping invalid key for detailed list: {spdxLower}")
            continue
        spdx, title, nickname, desc = (
            data.get("spdx_id", "N/A"),
            data.get("title", "N/A"),
            data.get("nickname"),
            data.get("description", "No description."),
        )
        permsTags, condsTags, limsTags = (
            data.get("permissions", []),
            data.get("conditions", []),
            data.get("limitations", []),
        )

        console.print(f"\n[bold cyan]SPDX ID:[/bold cyan] {spdx}")
        console.print(f"[bold]Title:[/bold] {title}")

        if nickname:
            console.print(f"[italic]Nickname:[/italic] {nickname}")
        console.print(
            f"[bold]Description:[/bold] {textwrap.shorten(desc or '', width=100, placeholder='...')}"
        )

        def GetRuleLabels(tags: list[str], category: str) -> list[str]:
            """
            Gets sorted rule labels for a list of tags and a category.

            Parameters
            ----------
            tags : list[str]
                List of rule tags.
            category : str
                Rule category ('permissions', 'conditions', 'limitations').

            Returns
            -------
            list[str]
                Sorted list of rule labels.
            """

            catRulesMap = rulesMap.get(category, {})

            return sorted([catRulesMap.get(tag, {}).get("label", tag) for tag in tags])

        permLabels, condLabels, limLabels = (
            GetRuleLabels(permsTags, "permissions"),
            GetRuleLabels(condsTags, "conditions"),
            GetRuleLabels(limsTags, "limitations"),
        )
        console.print(
            f"[bold green]Permissions[/bold green] ([blue]{len(permLabels)}[/blue]): {', '.join(permLabels) if permLabels else '[dim]None[/dim]'}"
        )
        console.print(
            f"[bold yellow]Conditions[/bold yellow] ([blue]{len(condLabels)}[/blue]): {', '.join(condLabels) if condLabels else '[dim]None[/dim]'}"
        )
        console.print(
            f"[bold red]Limitations[/bold red] ([blue]{len(limLabels)}[/blue]): {', '.join(limLabels) if limLabels else '[dim]None[/dim]'}"
        )

        if i < len(sortedKeys) - 1:
            console.print("[dim]---[/dim]")


def GetFullLicenseData(
    spdxIdLower: str, licensesData: dict[str, object]
) -> dict[str, object] | None:
    """
    Retrieves the full license data, including body, by fetching if not cached.

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license.
    licensesData : dict[str, object]
        The cache data containing all license information.

    Returns
    -------
    dict[str, object] | None
        A dictionary containing full license data (including 'body'), or None on error.
    """

    basicInfo = licensesData.get(spdxIdLower)

    if not basicInfo:
        console.print(
            f"[bold red]Error:[/bold red] Basic info for {spdxIdLower.upper()} not in cache."
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
                f"[yellow]Warning:[/yellow] Failed to parse cached content for {basicInfo.get('filename', spdxIdLower.upper())}. Re-fetching."
            )
            content = None

    if not content:
        filename = basicInfo.get("filename")

        if not filename:
            console.print(
                f"[bold red]Error:[/bold red] Filename missing for {spdxIdLower.upper()}. Cannot fetch."
            )

            return None

        VerbosePrint(f"Fetching full content for {filename} from GitHub...")
        # Optimization TODO: Store download_url in cache.
        githubFiles = FetchGithubDirListing(LICENSES_PATH)
        downloadUrl = None

        if githubFiles:

            for item in githubFiles:

                if isinstance(item, dict) and item.get("name") == filename:
                    downloadUrl = item.get("download_url")
                    break

        if not downloadUrl:
            console.print(
                f"[bold red]Error:[/bold red] Could not find download URL for {filename}."
            )

            return None

        content = FetchFileContent(downloadUrl)

        if content:
            fullLicenseData = ParseLicenseFile(filename, content)

        else:

            return None

    if not fullLicenseData:
        console.print(
            f"[bold red]Error:[/bold red] Failed to get full license data for {spdxIdLower.upper()}."
        )

        return None

    return fullLicenseData


def DisplayLicenseInfo(spdxIdLower: str, licensesData: dict[str, object]) -> None:
    """
    Displays detailed information about a specific license.

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license to display.
    licensesData : dict[str, object]
        The cache data containing all license, rules, and fields information.
    """

    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)

    if not fullLicenseData:

        return

    fm, spdxId, title, body = (
        fullLicenseData.get("front_matter", {}),
        fullLicenseData.get("spdx_id", "N/A"),
        fullLicenseData.get("front_matter", {}).get("title", "N/A"),
        fullLicenseData.get("body", ""),
    )
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
        """Prints a labeled text block if text is not None."""

        if text:
            console.print(f"\n[bold]{label}:[/bold]\n{textwrap.indent(text, '  ')}")

    PrintTextBlock("Description", fm.get("description"))
    PrintTextBlock("How to Apply", fm.get("how"))

    def PrintRulesWithLabels(
        label: str, key: str, rulesConfig: dict, color: str
    ) -> None:
        """Prints rules for a category with labels and descriptions."""
        ruleTags = fm.get(key, [])
        configRulesMap = {
            rule.get("tag"): rule
            for rule in rulesConfig.get(key, [])
            if isinstance(rule, dict) and rule.get("tag")
        }

        if ruleTags and isinstance(ruleTags, list):
            console.print(f"\n[bold {color}]{label}:[/bold {color}]")

            for tag in sorted(ruleTags):
                ruleInfo = configRulesMap.get(tag)

                if ruleInfo and ruleInfo.get("label"):
                    console.print(
                        f"  - [bold {color}]{ruleInfo['label']}[/bold {color}] ([dim]{tag}[/dim])"
                    )

                    if ruleInfo.get("description"):
                        console.print(
                            f"    [dim i]{textwrap.shorten(ruleInfo['description'], width=80, placeholder='...')}[/dim i]"
                        )

                else:
                    console.print(f"  - {tag} ([yellow]Label not found[/yellow])")

    PrintRulesWithLabels("Permissions", "permissions", rulesData, "green")
    PrintRulesWithLabels("Conditions", "conditions", rulesData, "yellow")
    PrintRulesWithLabels("Limitations", "limitations", rulesData, "red")

    if fm.get("using") and isinstance(fm["using"], dict) and fm["using"]:
        console.print("\n[bold]Notable Projects Using This License:[/bold]")

        for project, url in fm["using"].items():
            console.print(f"  - {project}: {url}")
    PrintTextBlock("Note", fm.get("note"))

    placeholders = FindPlaceholders(body)

    if placeholders:
        console.print("\n[bold]Placeholders in Body:[/bold]")

        for ph in sorted(list(placeholders)):
            phLower = ph.lower()
            fieldInfo = fieldsData.get(phLower)
            desc = fieldInfo.get("description", "No desc.") if fieldInfo else "No desc."
            argSugg = PLACEHOLDER_TO_ARG_MAP.get(phLower, f"(no arg for '[{ph}]')")
            defInfo = (
                " (defaults to current year)" if phLower in ["year", "yyyy"] else ""
            )
            console.print(
                f"  - [bold magenta][{ph}][/bold magenta]\n    [dim]Description:[/dim] {desc}\n    [dim]Argument:[/dim] {argSugg}{defInfo}"
            )

    else:
        console.print("\n[bold]Placeholders in Body:[/bold] [dim](None detected)[/dim]")


def DisplayLicenseSummaryAfterWrite(
    spdxIdLower: str,
    licensesData: dict[str, object],
    outputPath: Path,
    userProvidedForFilling: dict[str, str],
    cachedPlaceholdersAtStart: dict[str, str],
    filledLicenseBody: str,
) -> None:
    """
    Displays a summary of the license written to a file, including placeholder sources.

    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the written license.
    licensesData : dict[str, object]
        The cache data containing all license, rules, and fields information.
    outputPath : Path
        The path where the license file was written.
    userProvidedForFilling : dict[str, str]
        Standardized keys and values from CLI/defaults used for filling this run.
    cachedPlaceholdersAtStart : dict[str, str]
        Standardized keys and values from cache before this run.
    filledLicenseBody : str
        The final text of the license body that was written.
    """

    fullLicenseData = GetFullLicenseData(spdxIdLower, licensesData)

    if not fullLicenseData:
        console.print(
            f"\n--- License file written to [green]{outputPath}[/green], but summary unavailable. ---"
        )

        return

    fm = fullLicenseData.get("front_matter", {})
    title = fm.get("title", "N/A")
    # Original template body
    bodyTemplate = fullLicenseData.get("body", "")

    console.print(
        f"\n--- [bold]{title}[/bold] written to [green]{outputPath}[/green] ---"
    )

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    if fm.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {fm['nickname']}")

    def PrintTextBlock(label: str, text: str | None) -> None:
        """Prints a labeled text block if text is not None."""

        if text:
            console.print(f"\n[bold]{label}:[/bold]\n{textwrap.indent(text, '  ')}")

    PrintTextBlock("Description", fm.get("description"))

    def PrintRulesWithLabels(
        label: str, key: str, rulesConfig: dict, color: str
    ) -> None:
        """Prints rules for a category with labels."""
        ruleTags = fm.get(key, [])
        configRulesMap = {
            rule.get("tag"): rule
            for rule in rulesConfig.get(key, [])
            if isinstance(rule, dict) and rule.get("tag")
        }

        if ruleTags and isinstance(ruleTags, list):
            console.print(f"\n[bold {color}]{label}:[/bold {color}]")

            for tag in sorted(ruleTags):
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
    PrintTextBlock("Note", fm.get("note"))

    # Placeholder source reporting
    foundPlaceholdersInTemplate = FindPlaceholders(bodyTemplate)

    if foundPlaceholdersInTemplate:
        console.print("\n[bold]Placeholder Values Used:[/bold]")

        for phOriginalCase in sorted(list(foundPlaceholdersInTemplate)):
            phLower = phOriginalCase.lower()
            standardKey = RAW_PLACEHOLDER_TO_STANDARD_KEY.get(phLower)
            sourceInfo = ""
            # String representation of the value used
            valueUsedStr = ""

            if standardKey:
                # This dict has the final values used
                valueFromFillDict = userProvidedForFilling.get(standardKey)

                if valueFromFillDict is not None:
                    valueUsedStr = f' (Value: "{valueFromFillDict}")'

                if standardKey == "year":

                    if "year" in userProvidedForFilling and userProvidedForFilling[
                        "year"
                    ] != str(datetime.now().year):
                        sourceInfo = "[cyan]CLI argument (--year)[/cyan]"

                    else:
                        sourceInfo = "[blue]Defaulted (current year)[/blue]"
                # This case is tricky: value came from cache because no CLI arg was given for it.
                # We check if the value in userProvidedForFilling (which includes merged cache)
                # is the same as the original cache, AND that no CLI arg was actually provided for this key.
                elif (
                    standardKey in CLI_ARG_TO_CACHE_KEY.values()
                    and standardKey in userProvidedForFilling
                    and userProvidedForFilling.get(standardKey)
                    == cachedPlaceholdersAtStart.get(standardKey)
                    and standardKey
                    not in {
                        k
                        for k, v in CLI_ARG_TO_CACHE_KEY.items()
                        if getattr(parsedArguments, k, None) is not None
                    }
                ):
                    sourceInfo = f"[yellow]Saved preference (cache)[/yellow]"
                # Explicit CLI arg for non-year
                elif (
                    standardKey in CLI_ARG_TO_CACHE_KEY.values()
                    and standardKey in userProvidedForFilling
                ):
                    cliArgName = ""

                    for arg, sKey in CLI_ARG_TO_CACHE_KEY.items():

                        if sKey == standardKey:
                            # Find the actual CLI flag name, e.g. "--fullname"

                            for action in argumentParser._actions:

                                if action.dest == arg:
                                    cliArgName = (
                                        action.option_strings[0]
                                        if action.option_strings
                                        else arg
                                    )
                                    break
                            break
                    sourceInfo = f"[cyan]CLI argument ({cliArgName})[/cyan]"
                # Not by CLI, but was in cache
                elif standardKey in cachedPlaceholdersAtStart:
                    sourceInfo = f"[yellow]Saved preference (cache)[/yellow]"
                # Not CLI, not cache, not year default
                else:
                    sourceInfo = "[red]Not specified[/red]"

                    if f"[{phOriginalCase}]" in filledLicenseBody:
                        sourceInfo += " [bold red](remains in file!)[/bold red]"
                    # No specific value was "used" if it remains
                    valueUsedStr = ""
            # Placeholder in template not recognized by RAW_PLACEHOLDER_TO_STANDARD_KEY
            else:
                sourceInfo = "[magenta]Unknown placeholder[/magenta]"

                if f"[{phOriginalCase}]" in filledLicenseBody:
                    sourceInfo += " [bold red](remains in file!)[/bold red]"
                valueUsedStr = ""

            console.print(
                f"  - [bold magenta][{phOriginalCase}][/bold magenta]: {sourceInfo}{valueUsedStr}"
            )

    else:
        console.print(
            "\n[bold]Placeholder Values Used:[/bold] [dim](No standard placeholders in template)[/dim]"
        )


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """
    Compares multiple licenses based on key rule indicators.

    Parameters
    ----------
    spdxIdsLower : list[str]
        A list of lowercase SPDX IDs of licenses to compare.
    licensesData : dict[str, object]
        The cache data containing all license and rules information.
    """

    if len(spdxIdsLower) < 1:
        console.print(
            "\n[bold red]Error:[/bold red] Specify at least one license, or none to compare all."
        )

        return

    licensesToCompare, validSpdxIdsForComparison = [], []

    for spdxLower in spdxIdsLower:
        fullLicenseData = GetFullLicenseData(spdxLower, licensesData)

        if fullLicenseData:
            licensesToCompare.append(fullLicenseData)
            validSpdxIdsForComparison.append(spdxLower)

        else:
            VerbosePrint(
                f"Could not get data for {spdxLower.upper()}, skipping from comparison."
            )

    if len(licensesToCompare) < 2:

        if len(spdxIdsLower) == 1 and len(licensesToCompare) == 1:
            console.print(
                f"\n[yellow]Warning:[/yellow] Only one valid license ('{validSpdxIdsForComparison[0].upper()}') provided. Cannot compare."
            )

        elif not licensesToCompare and spdxIdsLower:
            console.print(
                "\n[bold red]Error:[/bold red] None of the specified licenses found for comparison."
            )

        else:
            console.print(
                "\n[bold red]Error:[/bold red] Need at least two valid licenses to compare."
            )

        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})

    if not rulesData:
        VerbosePrint(
            "[yellow]Warning:[/yellow] rules.yml not in cache. Rule context limited."
        )
    rulesMapByCategory = {}

    if rulesData:

        for catName, rulesList in rulesData.items():

            if catName in ["permissions", "conditions", "limitations"] and isinstance(
                rulesList, list
            ):

                for ruleDict in rulesList:

                    if isinstance(ruleDict, dict) and "tag" in ruleDict:
                        rulesMapByCategory[ruleDict["tag"]] = catName
    console.print(
        "Comparing:",
        ", ".join(f"[cyan]{lic['spdx_id']}[/cyan]" for lic in licensesToCompare),
    )
    indicatorTable = Table(title="Key Rule Indicators")
    indicatorTable.add_column("SPDX ID", justify="left", style="cyan", no_wrap=True)

    for label in KEY_RULES_FOR_COMPARISON.keys():
        indicatorTable.add_column(textwrap.fill(label, width=10), justify="center")

    for licData in licensesToCompare:
        spdxIdStr, fm = licData.get("spdx_id", "N/A"), licData.get("front_matter", {})
        perms, conds, lims = (
            fm.get("permissions", []),
            fm.get("conditions", []),
            fm.get("limitations", []),
        )
        rowIndicators = []

        for _, tagKey in KEY_RULES_FOR_COMPARISON.items():
            hasRule = False
            actualTag = tagKey

            if tagKey == "patent-use_perm":
                actualTag = "patent-use"
                hasRule = actualTag in perms

            elif tagKey == "patent-use_lim":
                actualTag = "patent-use"
                hasRule = actualTag in lims

            else:
                cat = rulesMapByCategory.get(actualTag)

                if cat == "permissions":
                    hasRule = actualTag in perms

                elif cat == "conditions":
                    hasRule = actualTag in conds

                elif cat == "limitations":
                    hasRule = actualTag in lims

                else:
                    hasRule = (
                        actualTag in perms or actualTag in conds or actualTag in lims
                    )
            rowIndicators.append(
                "[bold green]✓[/bold green]" if hasRule else "[bold red]X[/bold red]"
            )
        indicatorTable.add_row(spdxIdStr, *rowIndicators)
    console.print(indicatorTable)


def FindLicenses(
    requireTags: list[str] | None,
    disallowTags: list[str] | None,
    licensesData: dict[str, object],
) -> None:
    """
    Finds licenses that match required and disallowed rule tags.

    Parameters
    ----------
    requireTags : list[str] | None
        A list of rule tags that must be present in the license.
    disallowTags : list[str] | None
        A list of rule tags that must not be present in the license.
    licensesData : dict[str, object]
        The cache data containing all license and rules information.
    """

    requireTags, disallowTags = requireTags or [], disallowTags or []

    if not requireTags and not disallowTags:
        console.print(
            "\n[bold red]Error:[/bold red] Provide --require or --disallow for finding."
        )

        return

    licenseKeys = [
        k
        for k in licensesData.keys()
        if not k.startswith("data:") and k != USER_PLACEHOLDERS_CACHE_KEY
    ]

    if not licenseKeys:
        console.print("[yellow]No licenses found in cache.")
        return

    rulesData = licensesData.get("data:rules.yml", {}).get("content", {})

    if not rulesData:
        console.print(
            "[bold red]Error:[/bold red] rules.yml not in cache. Cannot validate tags."
        )
        return

    allValidTags = set()

    for category in ["permissions", "conditions", "limitations"]:
        allValidTags.update(
            rule.get("tag")
            for rule in rulesData.get(category, [])
            if isinstance(rule, dict) and rule.get("tag")
        )

    invalidReq = [t for t in requireTags if t not in allValidTags]
    invalidDis = [t for t in disallowTags if t not in allValidTags]

    if invalidReq or invalidDis:
        console.print("\n[bold red]Error:[/bold red] Invalid rule tags:")

        if invalidReq:
            console.print(f"  Invalid --require: {', '.join(invalidReq)}")

        if invalidDis:
            console.print(f"  Invalid --disallow: {', '.join(invalidDis)}")

        return

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
    matches = []

    for spdxLower in licenseKeys:
        data = licensesData[spdxLower]
        licenseRules = set(
            data.get("permissions", [])
            + data.get("conditions", [])
            + data.get("limitations", [])
        )

        if all(t in licenseRules for t in requireTags) and not any(
            t in licenseRules for t in disallowTags
        ):
            matches.append(data)

    if not matches:
        console.print("No licenses found matching all criteria.")

    else:
        console.print(f"Found {len(matches)} matching license(s):")

        for match in sorted(matches, key=lambda x: x.get("spdx_id", "")):
            console.print(
                f"  - [cyan]{match.get('spdx_id','N/A')}[/cyan] ({match.get('title','N/A')})"
            )


# Define parser globally so DisplayLicenseSummaryAfterWrite can access it for arg names
argumentParser = argparse.ArgumentParser(
    description="Fetch, display, compare, find, or fill open source license templates from github/choosealicense.com.",
    formatter_class=argparse.RawTextHelpFormatter,
    epilog=textwrap.dedent(
        f"""\
Examples:
  %(prog)s --list
  %(prog)s --info MIT
  %(prog)s --compare MIT Apache-2.0
  %(prog)s --find --require commercial-use
  %(prog)s --license MIT -f "Jane Doe" -o MyLicense.txt
  %(prog)s --set-placeholder fullname "My Org"
  %(prog)s --get-placeholder project
  %(prog)s --clear-placeholders email projecturl
  %(prog)s --clear-placeholders

Cached placeholder keys: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}
"""
    ),
)
# Will be populated by parser.parse_args()
parsedArguments = None


def main() -> int:
    """
    Main function to parse arguments and execute corresponding actions.

    Returns
    -------
    int
        Exit code (0 for success, 1 for error).
    """

    global isVerbose, isCacheModifiedByAction, parsedArguments, argumentParser

    argumentParser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh of local cache from GitHub.",
    )
    argumentParser.add_argument(
        "--cache-file",
        type=Path,
        default=Path(CACHE_FILENAME),
        help=f"Cache file (default: {CACHE_FILENAME}).",
    )
    argumentParser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output (to stderr)."
    )

    actionGroup = argumentParser.add_mutually_exclusive_group()
    actionGroup.add_argument(
        "-l",
        "--license",
        metavar="LICENSE_ID",
        help="SPDX ID of license template to fill.",
    )
    actionGroup.add_argument(
        "--list", nargs="*", metavar="LICENSE_ID", help="List licenses (all if no IDs)."
    )
    actionGroup.add_argument(
        "--detailed-list",
        nargs="*",
        metavar="LICENSE_ID",
        help="Detailed list (all if no IDs).",
    )
    actionGroup.add_argument(
        "--info", metavar="LICENSE_ID", help="Detailed metadata for a license."
    )
    actionGroup.add_argument(
        "--show-placeholders", metavar="LICENSE_ID", help="Placeholders for a license."
    )
    actionGroup.add_argument(
        "--compare",
        nargs="*",
        metavar="LICENSE_ID",
        help="Compare licenses (all if no IDs).",
    )
    actionGroup.add_argument(
        "--find", action="store_true", help="Find licenses by criteria."
    )
    actionGroup.add_argument(
        "--set-placeholder",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help=f"Save a placeholder value. KEY must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}.",
    )
    actionGroup.add_argument(
        "--get-placeholder",
        nargs="?",
        metavar="KEY",
        const="ALL_KEYS",
        help="Show saved placeholder value(s). Shows all if no KEY.",
    )
    actionGroup.add_argument(
        "--clear-placeholders",
        nargs="*",
        metavar="KEY",
        help=f"Clear saved placeholder(s). Clears all if no KEY. KEYs: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}.",
    )

    findGroup = argumentParser.add_argument_group("Options for --find")
    findGroup.add_argument(
        "--require",
        nargs="+",
        metavar="RULE_TAG",
        default=[],
        help="Rule tags that MUST be present.",
    )
    findGroup.add_argument(
        "--disallow",
        nargs="+",
        metavar="RULE_TAG",
        default=[],
        help="Rule tags that MUST NOT be present.",
    )

    fillGroup = argumentParser.add_argument_group(
        "Options for --license (override saved preferences)"
    )
    fillGroup.add_argument("-f", "--fullname", help="Full name of copyright holder.")
    fillGroup.add_argument(
        "-y", "--year", help="Copyright year (defaults to current year, not saved)."
    )
    fillGroup.add_argument("-p", "--project", help="Project name.")
    fillGroup.add_argument("-e", "--email", help="Email address.")
    fillGroup.add_argument("-u", "--projecturl", help="Project URL.")
    fillGroup.add_argument(
        "-o", "--output", help="Output file path (default: LICENSE)."
    )

    parsedArguments = argumentParser.parse_args()
    cacheFilePath = parsedArguments.cache_file
    isVerbose = parsedArguments.verbose

    licensesData, cacheUpdatedByFetch = UpdateAndLoadLicenseCache(
        cacheFilePath, parsedArguments.refresh
    )
    # Allow placeholder ops on empty/failed cache

    if not licensesData and not any(
        [
            parsedArguments.set_placeholder,
            parsedArguments.get_placeholder,
            parsedArguments.clear_placeholders,
        ]
    ):
        console.print(
            "[bold red]Error:[/bold red] Failed to load or update license data."
        )

        return 1

    userPlaceholdersCache = licensesData.get(USER_PLACEHOLDERS_CACHE_KEY, {})
    # Ensure it's a dict

    if not isinstance(userPlaceholdersCache, dict):
        VerbosePrint(
            f"[yellow]Warning:[/yellow] User placeholders in cache is not a dict. Resetting."
        )
        userPlaceholdersCache = {}
        licensesData[USER_PLACEHOLDERS_CACHE_KEY] = userPlaceholdersCache
        isCacheModifiedByAction = True

    def GetAllLicenseCacheKeys(cachedData: dict) -> list[str]:
        """
        Gets all license SPDX IDs (keys) from the cache data.

        Parameters
        ----------
        cachedData : dict
            The full cache data.

        Returns
        -------
        list[str]
            A list of lowercase SPDX ID keys for licenses.
        """

        return [
            k
            for k in cachedData.keys()
            if not k.startswith("data:") and k != USER_PLACEHOLDERS_CACHE_KEY
        ]

    actionTaken = False

    if parsedArguments.set_placeholder:
        actionTaken = True
        key, value = parsedArguments.set_placeholder

        if key not in CACHABLE_PLACEHOLDER_KEYS:
            console.print(
                f"[bold red]Error:[/bold red] Invalid placeholder key '{key}'. Must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}"
            )

            return 1

        userPlaceholdersCache[key] = value
        licensesData[USER_PLACEHOLDERS_CACHE_KEY] = userPlaceholdersCache
        isCacheModifiedByAction = True
        console.print(
            f"Placeholder [green]'{key}'[/green] set to [cyan]'{value}'[/cyan] in saved preferences."
        )

    elif parsedArguments.get_placeholder:
        actionTaken = True
        keyToGet = parsedArguments.get_placeholder

        if not userPlaceholdersCache:
            console.print("No saved placeholder preferences found.")

        elif keyToGet == "ALL_KEYS":
            console.print("[bold]Saved Placeholder Preferences:[/bold]")

            for k, v in sorted(userPlaceholdersCache.items()):
                console.print(f"  [green]{k}[/green]: [cyan]{v}[/cyan]")

        elif keyToGet in userPlaceholdersCache:
            console.print(
                f"[green]{keyToGet}[/green]: [cyan]{userPlaceholdersCache[keyToGet]}[/cyan]"
            )

        else:
            console.print(
                f"No saved preference found for key [yellow]'{keyToGet}'[/yellow]."
            )
            console.print(
                f"Available saved keys: {', '.join(sorted(userPlaceholdersCache.keys())) if userPlaceholdersCache else 'None'}"
            )

    # nargs='*' means it's a list
    elif parsedArguments.clear_placeholders is not None:
        actionTaken = True
        keysToClear = parsedArguments.clear_placeholders
        # Clear all

        if not keysToClear:

            if not userPlaceholdersCache:
                console.print("No saved placeholder preferences to clear.")

            else:
                userPlaceholdersCache.clear()
                isCacheModifiedByAction = True
                console.print("All saved placeholder preferences cleared.")

        else:
            clearedAny = False

            for keyToClear in keysToClear:

                if keyToClear not in CACHABLE_PLACEHOLDER_KEYS:
                    console.print(
                        f"[yellow]Warning:[/yellow] Invalid key '{keyToClear}' to clear. Skipping. Must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}"
                    )
                    continue

                if keyToClear in userPlaceholdersCache:
                    del userPlaceholdersCache[keyToClear]
                    isCacheModifiedByAction = True
                    clearedAny = True
                    console.print(
                        f"Cleared saved preference for [green]'{keyToClear}'[/green]."
                    )

                else:
                    console.print(
                        f"No saved preference found for key [yellow]'{keyToClear}'[/yellow] to clear."
                    )
            # User tried to clear valid keys but they weren't set

            if not clearedAny and any(
                k in CACHABLE_PLACEHOLDER_KEYS for k in keysToClear
            ):
                pass
            # User only provided invalid keys
            elif not clearedAny and not any(
                k in CACHABLE_PLACEHOLDER_KEYS for k in keysToClear
            ):
                pass
        licensesData[USER_PLACEHOLDERS_CACHE_KEY] = userPlaceholdersCache

    elif parsedArguments.list is not None:
        actionTaken = True
        targetKeys = []

        if not parsedArguments.list:
            targetKeys = GetAllLicenseCacheKeys(licensesData)

        else:

            for idStr in parsedArguments.list:
                idL = idStr.lower()

                if (
                    idL in licensesData
                    and not idL.startswith("data:")
                    and idL != USER_PLACEHOLDERS_CACHE_KEY
                ):
                    targetKeys.append(idL)

                else:
                    console.print(
                        f"[yellow]Warning:[/yellow] License '{idStr}' not found. Skipping."
                    )

        if not targetKeys and parsedArguments.list:
            console.print("[yellow]None of the specified licenses found.[/yellow]")

        elif not targetKeys and not parsedArguments.list:
            console.print("[yellow]No licenses in cache.[/yellow]")

        else:
            ListLicenses(licensesData, targetKeys)

    elif parsedArguments.detailed_list is not None:
        actionTaken = True
        targetKeys = []

        if not parsedArguments.detailed_list:
            targetKeys = GetAllLicenseCacheKeys(licensesData)

        else:

            for idStr in parsedArguments.detailed_list:
                idL = idStr.lower()

                if (
                    idL in licensesData
                    and not idL.startswith("data:")
                    and idL != USER_PLACEHOLDERS_CACHE_KEY
                ):
                    targetKeys.append(idL)

                else:
                    console.print(
                        f"[yellow]Warning:[/yellow] License '{idStr}' not found. Skipping."
                    )

        if not targetKeys and parsedArguments.detailed_list:
            console.print("[yellow]None of the specified licenses found.[/yellow]")

        elif not targetKeys and not parsedArguments.detailed_list:
            console.print("[yellow]No licenses in cache.[/yellow]")

        else:
            PrintDetailedList(licensesData, targetKeys)

    elif parsedArguments.find:
        actionTaken = True
        FindLicenses(parsedArguments.require, parsedArguments.disallow, licensesData)

    elif parsedArguments.info:
        actionTaken = True
        reqIdLower = parsedArguments.info.lower()

        if (
            not licensesData.get(reqIdLower)
            or reqIdLower.startswith("data:")
            or reqIdLower == USER_PLACEHOLDERS_CACHE_KEY
        ):
            console.print(
                f"\n[bold red]Error:[/bold red] License '{parsedArguments.info}' not found."
            )

            return 1

        DisplayLicenseInfo(reqIdLower, licensesData)

    elif parsedArguments.show_placeholders:
        actionTaken = True
        reqIdLower = parsedArguments.show_placeholders.lower()
        fullLicenseData = GetFullLicenseData(reqIdLower, licensesData)

        if not fullLicenseData:

            if not licensesData.get(reqIdLower):
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{parsedArguments.show_placeholders}' not found."
                )

            return 1

        basicInfo = licensesData.get(reqIdLower, {})
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        fieldsData = (
            {
                item["name"].lower(): item
                for item in fieldsDataList
                if isinstance(item, dict) and item.get("name")
            }
            if fieldsDataList
            else {}
        )
        console.print(
            f"\n[bold]--- Placeholders for {basicInfo.get('title','N/A')} ({basicInfo.get('spdx_id','N/A')}) ---[/bold]"
        )
        placeholders = FindPlaceholders(fullLicenseData.get("body", ""))

        if not placeholders:
            console.print("  [dim](No standard [placeholder] patterns found)[/dim]")

        else:

            for ph in sorted(list(placeholders)):
                phL = ph.lower()
                fieldInfo = fieldsData.get(phL)
                desc = (
                    fieldInfo.get("description", "No desc.")
                    if fieldInfo
                    else "No desc."
                )
                argSugg = PLACEHOLDER_TO_ARG_MAP.get(phL, f"(no arg for '[{ph}]')")
                defInfo = (
                    " (defaults to current year)" if phL in ["year", "yyyy"] else ""
                )
                console.print(
                    f"  - [bold magenta][{ph}][/bold magenta]\n    [dim]Description:[/dim] {desc}\n    [dim]Argument:[/dim] {argSugg}{defInfo}"
                )

    elif parsedArguments.compare is not None:
        actionTaken = True
        targetKeys = []

        if not parsedArguments.compare:
            targetKeys = GetAllLicenseCacheKeys(licensesData)

        else:

            for idStr in parsedArguments.compare:
                idL = idStr.lower()

                if (
                    idL in licensesData
                    and not idL.startswith("data:")
                    and idL != USER_PLACEHOLDERS_CACHE_KEY
                ):
                    targetKeys.append(idL)

                else:
                    console.print(
                        f"[yellow]Warning:[/yellow] License '{idStr}' not found. Skipping."
                    )

        if len(targetKeys) < 1 and parsedArguments.compare:
            console.print(
                "[yellow]None of the specified licenses found for comparison.[/yellow]"
            )

        elif len(targetKeys) < 2:
            console.print(
                f"\n[yellow]Warning:[/yellow] Need at least two licenses to compare. Found {len(targetKeys)}."
            )

        else:
            CompareLicenses(targetKeys, licensesData)

    elif parsedArguments.license:
        actionTaken = True
        reqIdLower = parsedArguments.license.lower()
        fullLicenseData = GetFullLicenseData(reqIdLower, licensesData)

        if not fullLicenseData:

            if not licensesData.get(reqIdLower):
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{parsedArguments.license}' not found."
                )

            return 1

        title, spdxId, body = (
            fullLicenseData.get("front_matter", {}).get("title", "N/A"),
            fullLicenseData.get("spdx_id", "N/A"),
            fullLicenseData.get("body", ""),
        )
        console.print(f"\nUsing license: [bold cyan]{title}[/bold cyan] ({spdxId})")

        # For summary reporting
        cachedPlaceholdersAtStart = userPlaceholdersCache.copy()

        # Collect CLI args for caching (non-year)
        userProvidedForCaching: dict[str, str] = {}

        if parsedArguments.fullname is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["fullname"]] = (
                parsedArguments.fullname
            )

        if parsedArguments.project is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["project"]] = (
                parsedArguments.project
            )

        if parsedArguments.email is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["email"]] = (
                parsedArguments.email
            )

        if parsedArguments.projecturl is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["projecturl"]] = (
                parsedArguments.projecturl
            )

        # Prepare final replacements for template filling
        finalTemplateReplacements: dict[str, str] = {}
        # 1. Start with cached (non-year)

        for key in CACHABLE_PLACEHOLDER_KEYS:

            if key in cachedPlaceholdersAtStart:
                finalTemplateReplacements[key] = cachedPlaceholdersAtStart[key]

        # 2. Override with CLI args (non-year)
        finalTemplateReplacements.update(userProvidedForCaching)

        # 3. Handle year (default or CLI)
        currentYearStr = str(datetime.now().year)
        yearToUse = (
            parsedArguments.year if parsedArguments.year is not None else currentYearStr
        )
        finalTemplateReplacements["year"] = yearToUse
        # Ensure related keys like 'yyyy' also get this year value for filling
        finalTemplateReplacements["yyyy"] = yearToUse
        # Also ensure fullname variations are covered

        if "fullname" in finalTemplateReplacements:
            finalTemplateReplacements["name of copyright owner"] = (
                finalTemplateReplacements["fullname"]
            )
            finalTemplateReplacements["login"] = finalTemplateReplacements["fullname"]

        # For summary: userProvidedForFilling includes explicit CLI args + year used
        userProvidedForFillingSummary = userProvidedForCaching.copy()
        # Add the year that was actually used
        userProvidedForFillingSummary["year"] = yearToUse

        filledLicense = FillLicenseTemplate(body, finalTemplateReplacements)
        outputPath = (
            Path(parsedArguments.output) if parsedArguments.output else Path("LICENSE")
        )

        try:

            outputPath.parent.mkdir(parents=True, exist_ok=True)

            with open(outputPath, "w", encoding="utf-8") as f:
                f.write(filledLicense + "\n")

        except IOError as e:
            console.print(
                f"\n[bold red]Error:[/bold red] writing to '{outputPath}': {e}"
            )

            return 1

        # Update placeholder cache if user provided specific args this run
        # If any cachable arg was given
        if userProvidedForCaching:
            userPlaceholdersCache.update(userProvidedForCaching)
            licensesData[USER_PLACEHOLDERS_CACHE_KEY] = userPlaceholdersCache
            isCacheModifiedByAction = True
            VerbosePrint("Updated saved placeholder preferences with CLI arguments.")

        DisplayLicenseSummaryAfterWrite(
            reqIdLower,
            licensesData,
            outputPath,
            userProvidedForFillingSummary,
            # Cache state before this run
            cachedPlaceholdersAtStart,
            filledLicense,
        )

    if not actionTaken:
        console.print("\n[bold red]Error:[/bold red] No action specified.")
        argumentParser.print_help(file=sys.stderr)

        return 1

    if cacheUpdatedByFetch or isCacheModifiedByAction:
        SaveCache(cacheFilePath, licensesData)
    # Only print if verbose and no other save message was printed
    elif isVerbose:
        console.print("No changes to save to cache file.")

    return 0


if __name__ == "__main__":

    sys.exit(main())
