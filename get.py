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

GITHUB_API_URL: str = "https://api.github.com"
OWNER: str = "github"
REPO: str = "choosealicense.com"
BRANCH: str = "gh-pages"
LICENSES_PATH: str = "_licenses"
DATA_PATH: str = "_data"
CACHE_FILENAME: str = "license_cache_rs.json"
USER_PLACEHOLDERS_CACHE_KEY: str = "user_placeholders"


CACHABLE_PLACEHOLDER_KEYS: list[str] = ["fullname", "project", "email", "projecturl"]
CLI_ARG_TO_CACHE_KEY: dict[str, str] = {
    "fullname": "fullname",
    "project": "project",
    "email": "email",
    "projecturl": "projecturl",
}
RAW_PLACEHOLDER_TO_STANDARD_KEY: dict[str, str] = {
    "fullname": "fullname",
    "name of copyright owner": "fullname",
    "login": "fullname",
    "project": "project",
    "email": "email",
    "projecturl": "projecturl",
    "year": "year",
    "yyyy": "year",
    "description": "description",
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
stdoutConsole = Console(highlight=False)


_verbose = False
_cacheModifiedByAction = False


argumentParser: argparse.ArgumentParser
parsedArgs: argparse.Namespace | None = None


def VerbosePrint(*args, **kwargs):
    """
    Prints output to the console if verbose mode is enabled.
    Parameters
    ----------
    *args
        Variable length argument list to print.
    **kwargs
        Arbitrary keyword arguments for console.print.
    """

    global _verbose
    if _verbose:
        console.print(*args, **kwargs)


def GetGithubApi(endpoint: str) -> dict | list | None:
    """
    Makes a GET request to the GitHub API.
    Parameters
    ----------
    endpoint : str
        The API endpoint to request (e.g., "/repos/owner/repo/contents/path").
    Returns
    -------
    dict | list | None
        The JSON response from the API as a dict or list, or None on error.
    """

    headers = {"Accept": "application/vnd.github.v3+json"}

    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"token {token}"
    url = f"{GITHUB_API_URL}{endpoint}"

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()

        return r.json()

    except requests.exceptions.Timeout:
        console.print(f"[bold red]Error:[/bold red] Timeout API ({url})")

        return None

    except requests.exceptions.RequestException as e:
        console.print(f"[bold red]Error:[/bold red] API ({url}): {e}")

        if hasattr(e, "response") and e.response is not None:
            console.print(
                f"Status: {e.response.status_code}, Body: {e.response.text[:200]}..."
            )

            if e.response.status_code == 403:
                console.print(
                    f"[yellow]Hint:[/yellow] Rate limits (Remaining: {e.response.headers.get('X-RateLimit-Remaining', 'N/A')}) or GITHUB_TOKEN."
                )

        return None

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Unexpected API error: {e}")

        return None


def FetchGithubDirListing(repoPath: str) -> list[dict[str, object]] | None:
    """
    Fetches the directory listing from a GitHub repository path.
    Parameters
    ----------
    repoPath : str
        The path within the repository (e.g., "_licenses").
    Returns
    -------
    list[dict[str, object]] | None
        A list of directory items, or None on error.
    """

    VerbosePrint(f"Fetching GitHub dir list ({repoPath})...")
    data = GetGithubApi(f"/repos/{OWNER}/{REPO}/contents/{repoPath}?ref={BRANCH}")

    if not isinstance(data, list):
        console.print(f"[bold red]Error:[/bold red] Bad dir list for {repoPath}")

        return None

    return data


def FetchFileContent(downloadUrl: str) -> str | None:
    """
    Fetches the content of a file from a given URL.
    Parameters
    ----------
    downloadUrl : str
        The URL to download the file content from.
    Returns
    -------
    str | None
        The text content of the file, or None on error.
    """

    try:
        r = requests.get(downloadUrl, timeout=10)
        r.raise_for_status()

        return r.text

    except requests.exceptions.Timeout:
        console.print(f"\n[bold red]Error:[/bold red] Timeout fetching {downloadUrl}")

        return None

    except requests.exceptions.RequestException as e:
        console.print(f"\n[bold red]Error:[/bold red] Fetching {downloadUrl}: {e}")

        return None

    except Exception as e:
        console.print(
            f"\n[bold red]Error:[/bold red] Unexpected error fetching {downloadUrl}: {e}"
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
        A dictionary containing "spdx_id", "front_matter", and "body", or None on error.
    """

    spdxId = None
    fm = {}
    body = fileContent.strip()

    if body.startswith("---"):
        parts = fileContent.split("---", 2)

        if len(parts) >= 3:

            try:
                fm = yaml.safe_load(parts[1].strip()) or {}

                if not isinstance(fm, dict):
                    fm = {}
                spdxId = fm.get("spdx-id")

            except yaml.YAMLError:
                VerbosePrint(f"[yellow]Warn:[/yellow] YAML error {filename}")
                fm = {}

        else:
            VerbosePrint(f"[yellow]Warn:[/yellow] Malformed front matter {filename}")

    if not spdxId:
        spdxId = GuessSpdxFromFilename(filename)

    if not spdxId and "spdx-id" in fm:
        spdxId = fm["spdx-id"]

    if not spdxId:
        console.print(f"[bold red]Error:[/bold red] No SPDX ID for {filename}")

        return None

    fm.setdefault("spdx-id", spdxId)
    fm.setdefault("title", spdxId)

    for k in ["nickname", "description", "how", "note", "using"]:
        fm.setdefault(k, None)

    for k in ["permissions", "conditions", "limitations"]:
        fm.setdefault(k, [])

    return {"spdx_id": spdxId, "front_matter": fm, "body": body}


def GuessSpdxFromFilename(filename: str) -> str | None:
    """
    Guesses the SPDX ID from the license filename.
    Parameters
    ----------
    filename : str
        The filename (e.g., "mit.txt").
    Returns
    -------
    str | None
        The guessed SPDX ID (e.g., "mit"), or None if not a valid format.
    """

    name = os.path.splitext(filename)[0]

    return name if re.match(r"^[A-Za-z0-9.\-\+]+$", name) else None


def ParseDataFile(filename: str, fileContent: str) -> object | None:
    """
    Parses a YAML data file.
    Parameters
    ----------
    filename : str
        The name of the data file (for context in messages).
    fileContent : str
        The raw text content of the data file.
    Returns
    -------
    object | None
        The parsed YAML content, or None on error.
    """

    try:

        return yaml.safe_load(fileContent)

    except yaml.YAMLError as e:
        console.print(f"[bold red]Error:[/bold red] YAML parse {filename}: {e}")

        return None

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] Unexpected parse {filename}: {e}")

        return None


def LoadCache(cacheFilePath: Path) -> dict[str, object]:
    """
    Loads license data from a JSON cache file.
    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.
    Returns
    -------
    dict[str, object]
        The loaded cache data, or an empty dictionary if an error occurs or file not found.
    """

    if cacheFilePath.exists():

        try:
            content = cacheFilePath.read_text(encoding="utf-8")

            return json.loads(content) if content else {}

        except (IOError, json.JSONDecodeError, Exception) as e:
            VerbosePrint(
                f"[yellow]Warn:[/yellow] Load/parse cache {cacheFilePath}: {e}."
            )

    return {}


def SaveCache(cacheFilePath: Path, cacheData: dict[str, object]) -> None:
    """
    Saves license data to a JSON cache file.
    Parameters
    ----------
    cacheFilePath : Path
        The path to the cache file.
    cacheData : dict[str, object]
        The data to save.
    """

    try:
        cacheFilePath.parent.mkdir(parents=True, exist_ok=True)

        with open(cacheFilePath, "w", encoding="utf-8") as f:
            json.dump(cacheData, f, indent=2, sort_keys=True)
        VerbosePrint(f"Cache saved to {cacheFilePath}")

    except (IOError, Exception) as e:
        console.print(f"[bold red]Error:[/bold red] Save cache {cacheFilePath}: {e}")


def GetParsedRulesComponent(
    fmRulesTags: list, categoryName: str, allRulesData: dict
) -> list:
    """
    Helper to create the parsed_rules structure for info_components.
    Parameters
    ----------
    fmRulesTags : list
        List of rule tags from the front matter for a specific category.
    categoryName : str
        The name of the rule category (e.g., "permissions").
    allRulesData : dict
        The complete parsed content of rules.yml.
    Returns
    -------
    list
        A list of dictionaries, each representing a rule with its tag, label, and description.
    """

    component = []
    categoryRulesMap = {
        r["tag"]: r
        for r in allRulesData.get(categoryName, [])
        if isinstance(r, dict) and "tag" in r
    }
    # Ensure unique tags from FM
    for tag in sorted(list(set(fmRulesTags))):
        ruleInfo = categoryRulesMap.get(tag)
        component.append(
            {
                "tag": tag,
                "label": ruleInfo.get("label", tag) if ruleInfo else tag,
                "description": (
                    ruleInfo.get("description", "No description available.")
                    if ruleInfo
                    else "No description available."
                ),
            }
        )

    return component


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
        If True, forces a full refresh of the cache, by default False.
    Returns
    -------
    tuple[dict[str, object], bool]
        A tuple containing the loaded/updated cache data and a boolean indicating if the cache was updated by fetching.
    """

    VerbosePrint("Loading cache...")
    cachedData = LoadCache(cacheFilePath) if not forceRefresh else {}

    if forceRefresh:
        VerbosePrint("Cache refresh forced.")

    cacheUpdatedByFetch = False
    # Holds data files like rules.yml, fields.yml
    processedDataFiles = {}
    dataFilesToFetch = []
    # Holds GitHub info for license files to fetch
    licenseFilesToFetch = []

    githubDataFiles = FetchGithubDirListing(DATA_PATH)

    if githubDataFiles:
        currentGithubDataFileItems = {
            item["name"]: item
            for item in githubDataFiles
            if isinstance(item, dict)
            and item.get("type") == "file"
            and item.get("name").endswith(".yml")
        }

        for name, ghInfo in currentGithubDataFileItems.items():
            cacheKey, cachedEntry = f"data:{name}", cachedData.get(f"data:{name}")

            if not ghInfo.get("sha") or not (
                isinstance(cachedEntry, dict)
                and cachedEntry.get("sha") == ghInfo.get("sha")
            ):
                dataFilesToFetch.append(ghInfo)
                cacheUpdatedByFetch = True
            # Keep valid cached entry
            else:
                processedDataFiles[cacheKey] = cachedEntry
        # Handle deleted data files
        # (Logic for handling deleted data files would go here)
    # Failed to fetch dir listing for data files
    else:
        VerbosePrint(
            "[yellow]Warn:[/yellow] Failed to fetch data file list. Using existing cache for data files."
        )

        for key, val in cachedData.items():

            if key.startswith("data:"):
                processedDataFiles[key] = val

    # Preserve user_placeholders from old cache if it exists
    if USER_PLACEHOLDERS_CACHE_KEY in cachedData:
        processedDataFiles[USER_PLACEHOLDERS_CACHE_KEY] = cachedData[
            USER_PLACEHOLDERS_CACHE_KEY
        ]

    # Fetch content for new/changed data files
    if dataFilesToFetch:

        for ghInfo in dataFilesToFetch:
            filename, downloadUrl = ghInfo.get("name", "unknown_data"), ghInfo.get(
                "download_url"
            )
            VerbosePrint(f"  Fetching data file: {filename}")
            content = FetchFileContent(downloadUrl)

            if (
                content
                and (parsedContent := ParseDataFile(filename, content)) is not None
            ):
                processedDataFiles[f"data:{filename}"] = {
                    "sha": ghInfo.get("sha"),
                    "content": parsedContent,
                }
                cacheUpdatedByFetch = True
            # Fetch/parse failed, keep old if exists
            elif f"data:{filename}" in cachedData:
                processedDataFiles[f"data:{filename}"] = cachedData[f"data:{filename}"]
                VerbosePrint(
                    f"  Using old cached version for {filename} due to fetch/parse error."
                )

    # Holds processed license data
    processedLicenses = {}
    # For enriching license cache
    allRulesDataContent = processedDataFiles.get("data:rules.yml", {}).get(
        "content", {}
    )

    githubLicenseFiles = FetchGithubDirListing(LICENSES_PATH)

    if githubLicenseFiles:
        currentGithubLicenseFileItems = {
            item["name"]: item
            for item in githubLicenseFiles
            if isinstance(item, dict)
            and item.get("type") == "file"
            and item.get("name").endswith(".txt")
        }

        for name, ghInfo in currentGithubLicenseFileItems.items():
            # Try to find existing cache entry by filename (SPDX ID might change if file is renamed but content is similar)
            cachedEntry = None
            oldSpdxKey = None

            for key, val in cachedData.items():

                if (
                    not key.startswith("data:")
                    and key != USER_PLACEHOLDERS_CACHE_KEY
                    and isinstance(val, dict)
                    and val.get("filename") == name
                ):
                    cachedEntry = val
                    oldSpdxKey = key
                    break

            if not ghInfo.get("sha") or not (
                cachedEntry and cachedEntry.get("sha") == ghInfo.get("sha")
            ):
                licenseFilesToFetch.append(ghInfo)
                cacheUpdatedByFetch = True
            # Valid, up-to-date cache entry
            elif cachedEntry:
                # Ensure SPDX ID key matches the one in content, important if SPDX ID in file changed
                currentSpdxInEntry = cachedEntry.get("spdx_id", "").lower()

                if oldSpdxKey != currentSpdxInEntry and currentSpdxInEntry:
                    processedLicenses[currentSpdxInEntry] = cachedEntry
                    VerbosePrint(
                        f"  Corrected cache key for {name} to {currentSpdxInEntry}"
                    )
                    # Cache structure changed
                    cacheUpdatedByFetch = True
                else:
                    processedLicenses[oldSpdxKey] = cachedEntry

        # Handle deleted license files
        # (Logic for handling deleted license files would go here)
    # Failed to fetch dir listing for licenses
    else:
        VerbosePrint(
            "[yellow]Warn:[/yellow] Failed to fetch license file list. Using existing cached licenses."
        )

        for key, val in cachedData.items():

            if not key.startswith("data:") and key != USER_PLACEHOLDERS_CACHE_KEY:
                processedLicenses[key] = val

    # Fetch content for new/changed license files
    if licenseFilesToFetch:
        progressColumns = [
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ]
        # Newline before progress
        console.print()

        with Progress(*progressColumns, console=console, transient=False) as progress:
            task = progress.add_task(
                "[cyan]Syncing licenses...", total=len(licenseFilesToFetch)
            )

            for ghInfo in licenseFilesToFetch:
                filename, downloadUrl = ghInfo.get("name", "unknown.txt"), ghInfo.get(
                    "download_url"
                )
                progress.update(task, description=f"[cyan]Fetching: {filename}")
                content = FetchFileContent(downloadUrl)

                if content and (parsedFile := ParseLicenseFile(filename, content)):
                    spdxLower = parsedFile["spdx_id"].lower()
                    fm = parsedFile["front_matter"]
                    body = parsedFile["body"]

                    infoComps = {
                        "how_to_apply_text": fm.get("how"),
                        "note_text": fm.get("note"),
                        "using_info": fm.get("using"),
                        "parsed_rules": {
                            cat: GetParsedRulesComponent(
                                fm.get(cat, []), cat, allRulesDataContent
                            )
                            for cat in ["permissions", "conditions", "limitations"]
                        },
                    }
                    processedLicenses[spdxLower] = {
                        "spdx_id": parsedFile["spdx_id"],
                        "title": fm.get("title"),
                        "nickname": fm.get("nickname"),
                        "description": fm.get("description"),
                        "filename": filename,
                        "sha": ghInfo.get("sha"),
                        # Keep raw tags for --find
                        "permissions": fm.get("permissions", []),
                        "conditions": fm.get("conditions", []),
                        "limitations": fm.get("limitations", []),
                        # Full raw content
                        "file_content_cached": content,
                        "placeholders_in_body": sorted(list(FindPlaceholders(body))),
                        "info_components": infoComps,
                    }
                    cacheUpdatedByFetch = True
                # Fetch/parse failed, keep old if exists by filename
                elif filename in {
                    entry.get("filename")
                    for entry in cachedData.values()
                    if isinstance(entry, dict)
                }:
                    # Find the old entry and carry it over
                    for key, val_dict in cachedData.items():

                        if (
                            isinstance(val_dict, dict)
                            and val_dict.get("filename") == filename
                            and not key.startswith("data:")
                            and key != USER_PLACEHOLDERS_CACHE_KEY
                        ):
                            processedLicenses[key] = val_dict
                            VerbosePrint(
                                f"  Using old cached version for {filename} due to fetch/parse error."
                            )
                            break
                progress.advance(task)
        # Newline after progress
        console.print()

    finalCacheData = {**processedDataFiles, **processedLicenses}
    # Ensure it's at top level
    if USER_PLACEHOLDERS_CACHE_KEY in processedDataFiles:
        finalCacheData[USER_PLACEHOLDERS_CACHE_KEY] = processedDataFiles.pop(
            USER_PLACEHOLDERS_CACHE_KEY
        )

    if not cacheUpdatedByFetch and not forceRefresh:
        VerbosePrint("Cache is up-to-date regarding remote files.")

    return finalCacheData, cacheUpdatedByFetch


def FindPlaceholders(templateBody: str) -> set[str]:
    """
    Finds all unique placeholder strings in a template body.
    Parameters
    ----------
    templateBody : str
        The text content of the license template.
    Returns
    -------
    set[str]
        A set of unique placeholder strings found (e.g., {"[fullname]", "[year]"}).
    """

    # Returns raw placeholder strings e.g. "[fullname]"
    return set(re.findall(r"\[([^\]]+)\]", templateBody))


def FillLicenseTemplate(templateBody: str, replacements: dict[str, str]) -> str:
    """
    Fills placeholders in a license template body with provided replacement values.
    Parameters
    ----------
    templateBody : str
        The raw license template text.
    replacements : dict[str, str]
        A dictionary where keys are standardized placeholder keys (e.g., "year", "fullname")
        and values are the strings to insert.
    Returns
    -------
    str
        The license text with placeholders filled.
    """

    filledText = templateBody
    # `replacements` keys are standardized e.g. "year", "fullname"
    for phStandardKey, valueToInsert in replacements.items():
        # Find all raw placeholders in the body that map to this standard key
        # e.g. "[Year]", "[yyyy]"
        for rawPhInBodyFullMatch in FindPlaceholders(filledText):
            rawPhInBodyNoBrackets = rawPhInBodyFullMatch.strip("[]")

            if (
                RAW_PLACEHOLDER_TO_STANDARD_KEY.get(rawPhInBodyNoBrackets.lower())
                == phStandardKey
            ):
                filledText = filledText.replace(
                    rawPhInBodyFullMatch, str(valueToInsert)
                )

    return filledText


def ListLicenses(licensesData: dict[str, object], targetLicenseKeys: list[str]) -> None:
    """
    Lists available licenses with their SPDX ID and title.
    Parameters
    ----------
    licensesData : dict[str, object]
        The cache data containing all license information.
    targetLicenseKeys : list[str]
        A list of lowercase SPDX IDs of licenses to list.
    """

    if not targetLicenseKeys:
        console.print("[yellow]No licenses found/specified.[/yellow]")

        return

    console.print(
        "\n[bold]Available Licenses (SPDX ID: Title):[/bold]\n[dim]"
        + ("-" * 50)
        + "[/dim]"
    )

    for spdxLower in sorted(targetLicenseKeys):
        data = licensesData.get(spdxLower)

        if isinstance(data, dict):
            console.print(
                f"  [cyan]{data.get('spdx_id', 'N/A'):<25}[/cyan] : {data.get('title', 'N/A')}"
            )


def PrintDetailedList(
    licensesData: dict[str, object], targetLicenseKeys: list[str]
) -> None:
    """
    Prints a detailed list of specified licenses, including rules.
    Parameters
    ----------
    licensesData : dict[str, object]
        The cache data containing all license information.
    targetLicenseKeys : list[str]
        A list of lowercase SPDX IDs of licenses to detail.
    """

    if not targetLicenseKeys:
        console.print("[yellow]No licenses found/specified.[/yellow]")

        return

    for i, spdxLower in enumerate(sorted(targetLicenseKeys)):
        data = licensesData.get(spdxLower)

        if not isinstance(data, dict):
            continue

        console.print(f"\n[bold cyan]SPDX ID:[/bold cyan] {data.get('spdx_id', 'N/A')}")
        console.print(f"[bold]Title:[/bold] {data.get('title', 'N/A')}")

        if data.get("nickname"):
            console.print(f"[italic]Nickname:[/italic] {data.get('nickname')}")
        console.print(
            f"[bold]Description:[/bold] {textwrap.shorten(data.get('description','') or '', width=100, placeholder='...')}"
        )

        parsedRules = data.get("info_components", {}).get("parsed_rules", {})

        for catName, color, rulesList in [
            ("permissions", "green", parsedRules.get("permissions", [])),
            ("conditions", "yellow", parsedRules.get("conditions", [])),
            ("limitations", "red", parsedRules.get("limitations", [])),
        ]:
            labels = [r["label"] for r in rulesList]
            console.print(
                f"[bold {color}]{catName.capitalize()}[/bold {color}] ([blue]{len(labels)}[/blue]): {', '.join(labels) if labels else '[dim]None[/dim]'}"
            )

        if i < len(sorted(targetLicenseKeys)) - 1:
            console.print("[dim]---[/dim]")


def GetFullLicenseData(
    spdxIdLower: str, licensesData: dict[str, object]
) -> dict[str, object] | None:
    """
    Ensures a rich license entry is returned, potentially fetching content if missing from cache.
    The main cache is NOT updated by this function; that's UpdateAndLoadLicenseCache's job.
    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license.
    licensesData : dict[str, object]
        The main cache data.
    Returns
    -------
    dict[str, object] | None
        A rich license entry dictionary, or None if not found or critical data is missing.
    """

    licenseEntry = licensesData.get(spdxIdLower)

    if not isinstance(licenseEntry, dict):
        console.print(
            f"[bold red]Error:[/bold red] License '{spdxIdLower.upper()}' not found in cache index."
        )

        return None

    # If essential components are missing (e.g. from an old cache format or partial load)
    # try to reconstruct them for this return value.
    # This is a fallback, ideally UpdateAndLoadLicenseCache populates everything.
    needsReconstruction = not all(
        k in licenseEntry
        for k in ["file_content_cached", "placeholders_in_body", "info_components"]
    )

    if needsReconstruction and "filename" in licenseEntry:
        VerbosePrint(
            f"Cache entry for {spdxIdLower} is partial, attempting to reconstruct..."
        )
        # Try to get download_url (this is inefficient here, ideally stored in cache)
        dlUrl = None

        if ghFiles := FetchGithubDirListing(LICENSES_PATH):

            for item in ghFiles:

                if (
                    isinstance(item, dict)
                    and item.get("name") == licenseEntry["filename"]
                ):
                    dlUrl = item.get("download_url")
                    break

        if dlUrl and (content := FetchFileContent(dlUrl)):

            if parsedFile := ParseLicenseFile(licenseEntry["filename"], content):
                # Reconstruct a temporary full entry
                # This is a simplified reconstruction; UpdateAndLoadLicenseCache does it more thoroughly
                # Start with what we have
                tempEntry = licenseEntry.copy()
                tempEntry["file_content_cached"] = content
                tempEntry["placeholders_in_body"] = sorted(
                    list(FindPlaceholders(parsedFile["body"]))
                )

                fm = parsedFile["front_matter"]
                allRulesData = licensesData.get("data:rules.yml", {}).get("content", {})
                tempEntry["info_components"] = {
                    "how_to_apply_text": fm.get("how"),
                    "note_text": fm.get("note"),
                    "using_info": fm.get("using"),
                    "parsed_rules": {
                        cat: GetParsedRulesComponent(fm.get(cat, []), cat, allRulesData)
                        for cat in ["permissions", "conditions", "limitations"]
                    },
                }
                # Update other base fields from fresh parse
                for k in [
                    "spdx_id",
                    "title",
                    "nickname",
                    "description",
                    "permissions",
                    "conditions",
                    "limitations",
                ]:

                    if k in fm:
                        tempEntry[k] = fm[k]

                VerbosePrint(
                    f"Reconstructed entry for {spdxIdLower} for this operation."
                )
                # Return the temporarily reconstructed entry
                return tempEntry

            else:
                console.print(
                    f"[bold red]Error:[/bold red] Failed to parse re-fetched content for {spdxIdLower}."
                )

        else:
            console.print(
                f"[bold red]Error:[/bold red] Could not re-fetch content for {spdxIdLower} to get full data."
            )
            # Cannot proceed if content is missing and cannot be fetched/parsed
            return None

    # Critical content missing
    elif not isinstance(licenseEntry.get("file_content_cached"), str):
        console.print(
            f"[bold red]Error:[/bold red] Cached content for {spdxIdLower.upper()} is missing or invalid."
        )

        return None

    # Return the (hopefully) rich entry from cache
    return licenseEntry


def DisplayLicenseInfo(spdxIdLower: str, licensesData: dict[str, object]) -> None:
    """
    Displays detailed information about a specific license.
    Parameters
    ----------
    spdxIdLower : str
        The lowercase SPDX ID of the license to display.
    licensesData : dict[str, object]
        The cache data containing all license information.
    """

    # Use GetFullLicenseData to ensure we have a rich entry
    licenseEntry = GetFullLicenseData(spdxIdLower, licensesData)

    if not licenseEntry:

        return

    title = licenseEntry.get("title", "N/A")
    spdxId = licenseEntry.get("spdx_id", "N/A")
    infoComps = licenseEntry.get("info_components", {})

    console.print(f"\n[bold]--- License Information: {title} ({spdxId}) ---[/bold]")

    if licenseEntry.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {licenseEntry['nickname']}")

    def PrintTextBlock(label: str, text: str | None):
        if text:
            console.print(f"\n[bold]{label}:[/bold]\n{textwrap.indent(text, '  ')}")

    PrintTextBlock("Description", licenseEntry.get("description"))
    PrintTextBlock("How to Apply", infoComps.get("how_to_apply_text"))

    parsedRules = infoComps.get("parsed_rules", {})

    for catName, color, rulesList in [
        ("Permissions", "green", parsedRules.get("permissions", [])),
        ("Conditions", "yellow", parsedRules.get("conditions", [])),
        ("Limitations", "red", parsedRules.get("limitations", [])),
    ]:

        if rulesList:
            console.print(f"\n[bold {color}]{catName}:[/bold {color}]")
            # rule is now dict with tag, label, description
            for rule in rulesList:
                console.print(
                    f"  - [bold {color}]{rule['label']}[/bold {color}] ([dim]{rule['tag']}[/dim])"
                )

                if rule["description"]:
                    console.print(
                        f"    [dim i]{textwrap.shorten(rule['description'], width=80, placeholder='...')}[/dim i]"
                    )

    if using := infoComps.get("using_info"):

        if isinstance(using, dict) and using:
            console.print("\n[bold]Notable Projects Using This License:[/bold]")

            for project, url in using.items():
                console.print(f"  - {project}: {url}")
    PrintTextBlock("Note", infoComps.get("note_text"))

    placeholdersInBody = licenseEntry.get("placeholders_in_body", [])
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    if placeholdersInBody:
        console.print("\n[bold]Placeholders in Body:[/bold]")
        # e.g., "[fullname]"
        for phFullStr in placeholdersInBody:
            phNoBrackets = phFullStr.strip("[]")
            phLower = phNoBrackets.lower()
            fieldInfo = fieldsData.get(phLower)
            desc = fieldInfo.get("description", "No desc.") if fieldInfo else "No desc."
            argSugg = PLACEHOLDER_TO_ARG_MAP.get(phLower, f"(no arg for '{phFullStr}')")
            defInfo = (
                " (defaults to current year)" if phLower in ["year", "yyyy"] else ""
            )
            console.print(
                f"  - [bold magenta]{phFullStr}[/bold magenta]\n    [dim]Description:[/dim] {desc}\n    [dim]Argument:[/dim] {argSugg}{defInfo}"
            )

    else:
        console.print("\n[bold]Placeholders in Body:[/bold] [dim](None detected)[/dim]")


def DisplayLicenseSummaryAfterWrite(
    licenseEntry: dict,
    licensesData: dict[str, object],
    outputPath: Path,
    userProvidedForFilling: dict[str, str],
    cachedPlaceholdersAtStart: dict[str, str],
    filledLicenseBody: str,
) -> None:
    """
    Displays a summary after a license file has been written.
    Parameters
    ----------
    licenseEntry : dict
        The rich license entry from the cache.
    outputPath : Path
        The path where the license file was written.
    userProvidedForFilling : dict[str, str]
        Placeholder values provided by the user for this write operation.
    cachedPlaceholdersAtStart : dict[str, str]
        Placeholder values that were cached before this operation.
    filledLicenseBody : str
        The final content written to the license file.
    """

    title = licenseEntry.get("title", "N/A")
    console.print(
        f"\n--- [bold]{title}[/bold] written to [green]{outputPath}[/green] ---"
    )

    infoComps = licenseEntry.get("info_components", {})

    if licenseEntry.get("nickname"):
        console.print(f"\n[italic]Nickname:[/italic] {licenseEntry['nickname']}")

    def PrintTextBlock(label: str, text: str | None):
        if text:
            console.print(f"\n[bold]{label}:[/bold]\n{textwrap.indent(text, '  ')}")

    PrintTextBlock("Description", licenseEntry.get("description"))

    parsedRules = infoComps.get("parsed_rules", {})

    for catName, color, rulesList in [
        ("Permissions", "green", parsedRules.get("permissions", [])),
        ("Conditions", "yellow", parsedRules.get("conditions", [])),
        ("Limitations", "red", parsedRules.get("limitations", [])),
    ]:

        if rulesList:
            console.print(f"\n[bold {color}]{catName}:[/bold {color}]")

            for rule in rulesList:
                console.print(
                    f"  - [bold {color}]{rule['label']}[/bold {color}] ([dim]{rule['tag']}[/dim])"
                )
    PrintTextBlock("Note", infoComps.get("note_text"))

    foundPlaceholdersInTemplate = licenseEntry.get("placeholders_in_body", [])
    # For descriptions
    fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
    fieldsData = {
        item["name"].lower(): item
        for item in fieldsDataList
        if isinstance(item, dict) and item.get("name")
    }

    if foundPlaceholdersInTemplate:
        console.print("\n[bold]Placeholder Values Used:[/bold]")
        # e.g. "[fullname]"
        for phFullStr in foundPlaceholdersInTemplate:
            phNoBrackets = phFullStr.strip("[]")
            phLower = phNoBrackets.lower()
            standardKey = RAW_PLACEHOLDER_TO_STANDARD_KEY.get(phLower)
            sourceInfo, valueUsedStr = "", ""

            if standardKey:
                valueActuallyUsed = userProvidedForFilling.get(standardKey)

                if valueActuallyUsed is not None:
                    valueUsedStr = f' (Value: "{valueActuallyUsed}")'

                if standardKey == "year":
                    sourceInfo = (
                        "[cyan]CLI argument (--year)[/cyan]"
                        if parsedArgs.year
                        else "[blue]Defaulted (current year)[/blue]"
                    )
                elif (
                    standardKey in userProvidedForFilling
                    and getattr(parsedArgs, standardKey, None) is not None
                ):
                    cliArgName = ""

                    for argDest, sKeyMap in CLI_ARG_TO_CACHE_KEY.items():

                        if sKeyMap == standardKey:

                            for action in argumentParser._actions:

                                if action.dest == argDest:
                                    cliArgName = (
                                        action.option_strings[0]
                                        if action.option_strings
                                        else argDest
                                    )
                                    break

                            break
                    sourceInfo = (
                        f"[cyan]CLI argument ({cliArgName})[/cyan]"
                        if cliArgName
                        else f"[cyan]CLI argument[/cyan]"
                    )
                elif standardKey in cachedPlaceholdersAtStart:
                    sourceInfo = f"[yellow]Saved preference (cache)[/yellow]"
                else:
                    sourceInfo = "[red]Not specified[/red]"

                    if phFullStr in filledLicenseBody:
                        sourceInfo += " [bold red](remains in file!)[/bold red]"
                    valueUsedStr = ""
            else:
                sourceInfo = "[magenta]Unknown placeholder[/magenta]"

                if phFullStr in filledLicenseBody:
                    sourceInfo += " [bold red](remains in file!)[/bold red]"
            console.print(
                f"  - [bold magenta]{phFullStr}[/bold magenta]: {sourceInfo}{valueUsedStr}"
            )

    else:
        console.print(
            "\n[bold]Placeholder Values Used:[/bold] [dim](No standard placeholders in template)[/dim]"
        )


def CompareLicenses(spdxIdsLower: list[str], licensesData: dict[str, object]) -> None:
    """
    Compares specified licenses based on key rule indicators.
    Parameters
    ----------
    spdxIdsLower : list[str]
        A list of lowercase SPDX IDs of licenses to compare.
    licensesData : dict[str, object]
        The cache data containing all license information.
    """

    if len(spdxIdsLower) < 1:
        console.print("\n[bold red]Error:[/bold red] Specify licenses or none for all.")

        return

    licensesToCompare = []

    for spdxL in spdxIdsLower:
        # GetFullLicenseData ensures we get a usable entry
        if licEntry := GetFullLicenseData(spdxL, licensesData):
            licensesToCompare.append(licEntry)
        else:
            VerbosePrint(f"Skipping {spdxL.upper()} from comparison (no data).")

    if len(licensesToCompare) < 2:
        console.print(
            f"\n[yellow]Warning:[/yellow] Need >= 2 licenses to compare. Got {len(licensesToCompare)}."
        )

        return

    console.print(
        "Comparing:",
        ", ".join(f"[cyan]{lic['spdx_id']}[/cyan]" for lic in licensesToCompare),
    )
    table = Table(title="Key Rule Indicators")
    table.add_column("SPDX ID", style="cyan", no_wrap=True)

    for label in KEY_RULES_FOR_COMPARISON.keys():
        table.add_column(textwrap.fill(label, 10), justify="center")

    for licData in licensesToCompare:
        # These tags are still directly on the licenseEntry from cache
        perms, conds, lims = (
            licData.get("permissions", []),
            licData.get("conditions", []),
            licData.get("limitations", []),
        )
        row = [licData.get("spdx_id", "N/A")]

        for _, tagKey in KEY_RULES_FOR_COMPARISON.items():
            has = False

            if tagKey == "patent-use_perm":
                has = "patent-use" in perms
            elif tagKey == "patent-use_lim":
                has = "patent-use" in lims
            # Basic check
            elif tagKey in perms or tagKey in conds or tagKey in lims:
                has = True
            row.append(
                "[bold green]✓[/bold green]" if has else "[bold red]X[/bold red]"
            )
        table.add_row(*row)
    console.print(table)


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
        A list of rule tags that licenses must have.
    disallowTags : list[str] | None
        A list of rule tags that licenses must not have.
    licensesData : dict[str, object]
        The cache data containing all license information.
    """

    req, dis = requireTags or [], disallowTags or []

    if not req and not dis:
        console.print("\n[bold red]Error:[/bold red] Provide --require or --disallow.")

        return

    # Filter out non-license entries for iteration
    validLicenseKeys = [
        k
        for k, v in licensesData.items()
        if not k.startswith("data:")
        and k != USER_PLACEHOLDERS_CACHE_KEY
        and isinstance(v, dict)
    ]

    if not validLicenseKeys:
        console.print("[yellow]No licenses in cache.")

        return

    rulesYmlContent = licensesData.get("data:rules.yml", {}).get("content", {})

    if not rulesYmlContent:
        console.print(
            "[bold red]Error:[/bold red] rules.yml not in cache for tag validation."
        )

        return

    allValidTags = set()
    # permissions, conditions, limitations
    for catRules in rulesYmlContent.values():

        if isinstance(catRules, list):

            for rule in catRules:

                if isinstance(rule, dict) and "tag" in rule:
                    allValidTags.add(rule["tag"])

    invalidReq = [t for t in req if t not in allValidTags]
    invalidDis = [t for t in dis if t not in allValidTags]

    if invalidReq or invalidDis:
        console.print("\n[bold red]Error:[/bold red] Invalid tags:")

        if invalidReq:
            console.print(f"  Require: {', '.join(invalidReq)}")

        if invalidDis:
            console.print(f"  Disallow: {', '.join(invalidDis)}")

        return

    console.print(
        "Require:", f"[green]{', '.join(req)}[/green]" if req else "[dim]None[/dim]"
    )
    console.print(
        "Disallow:", f"[red]{', '.join(dis)}[/red]" if dis else "[dim]None[/dim]"
    )
    console.print("[dim]" + ("-" * 50) + "[/dim]")

    matches = []

    for key in validLicenseKeys:
        # data is the licenseEntry dict
        data = licensesData[key]
        # These tags are directly on the licenseEntry
        licenseRules = set(
            data.get("permissions", [])
            + data.get("conditions", [])
            + data.get("limitations", [])
        )

        if all(t in licenseRules for t in req) and not any(
            t in licenseRules for t in dis
        ):
            matches.append(data)

    if not matches:
        console.print("No licenses found matching criteria.")

    else:
        console.print(f"Found {len(matches)} matching license(s):")

        for match in sorted(matches, key=lambda x: x.get("spdx_id", "")):
            console.print(
                f"  - [cyan]{match.get('spdx_id','N/A')}[/cyan] ({match.get('title','N/A')})"
            )


def main() -> int:
    global _verbose, _cacheModifiedByAction, parsedArgs, argumentParser

    argumentParser = argparse.ArgumentParser(
        description="Manage open source license templates from github/choosealicense.com.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            f"""\
    Examples:
      %(prog)s --list
      %(prog)s --info MIT
      %(prog)s --license MIT -f "Jane Doe"
      %(prog)s --set-placeholder fullname "My Org"
      %(prog)s --get-placeholder
    Cached placeholder keys: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}"""
        ),
    )

    argumentParser.add_argument(
        "--refresh", action="store_true", help="Force refresh cache."
    )
    argumentParser.add_argument(
        "--cache-file",
        type=Path,
        default=Path(CACHE_FILENAME),
        help=f"Cache file (def: {CACHE_FILENAME}).",
    )
    argumentParser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output."
    )
    # Make one action required
    actionGroup = argumentParser.add_mutually_exclusive_group(required=True)
    actionGroup.add_argument(
        "-l", "--license", metavar="ID", help="Fill license template."
    )
    actionGroup.add_argument(
        "--list", nargs="*", metavar="ID", help="List licenses (all if no IDs)."
    )
    actionGroup.add_argument(
        "--detailed-list",
        nargs="*",
        metavar="ID",
        help="Detailed list (all if no IDs).",
    )
    actionGroup.add_argument(
        "--info", metavar="ID", help="Detailed metadata for a license."
    )
    actionGroup.add_argument(
        "--show-placeholders", metavar="ID", help="Placeholders for a license."
    )
    actionGroup.add_argument(
        "--compare", nargs="*", metavar="ID", help="Compare licenses (all if no IDs)."
    )
    actionGroup.add_argument(
        "--find", action="store_true", help="Find licenses by criteria."
    )
    actionGroup.add_argument(
        "--set-placeholder",
        nargs=2,
        metavar=("K", "V"),
        help=f"Save placeholder. K: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}.",
    )
    actionGroup.add_argument(
        "--get-placeholder",
        nargs="?",
        metavar="K",
        const="ALL_KEYS",
        help="Show saved placeholder(s).",
    )
    actionGroup.add_argument(
        "--clear-placeholders",
        nargs="*",
        metavar="K",
        help=f"Clear placeholder(s). K: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}.",
    )
    findGroup = argumentParser.add_argument_group("Options for --find")
    findGroup.add_argument(
        "--require", nargs="+", metavar="TAG", default=[], help="Required rule tags."
    )
    findGroup.add_argument(
        "--disallow", nargs="+", metavar="TAG", default=[], help="Disallowed rule tags."
    )
    fillGroup = argumentParser.add_argument_group(
        "Options for --license (override saved)"
    )
    fillGroup.add_argument("-f", "--fullname", help="Full name.")
    fillGroup.add_argument("-y", "--year", help="Year (def: current, not saved).")
    fillGroup.add_argument("-p", "--project", help="Project name.")
    fillGroup.add_argument("-e", "--email", help="Email.")
    fillGroup.add_argument("-u", "--projecturl", help="Project URL.")
    fillGroup.add_argument("-o", "--output", help="Output file (def: LICENSE).")

    parsedArgs = argumentParser.parse_args()
    _verbose = parsedArgs.verbose
    cacheFilePath = parsedArgs.cache_file

    licensesData, cacheUpdatedByFetch = UpdateAndLoadLicenseCache(
        cacheFilePath, parsedArgs.refresh
    )
    # Ensure user_placeholders key exists and is a dict
    userPlaceholdersCache = licensesData.setdefault(USER_PLACEHOLDERS_CACHE_KEY, {})

    if not isinstance(userPlaceholdersCache, dict):
        VerbosePrint(
            "[yellow]Warn:[/yellow] User placeholders in cache not a dict. Resetting."
        )
        userPlaceholdersCache = {}
        licensesData[USER_PLACEHOLDERS_CACHE_KEY] = userPlaceholdersCache
        # Mark for saving
        _cacheModifiedByAction = True

    def GetAllLicenseCacheKeys(cachedData: dict) -> list[str]:
        return [
            k
            for k, v in cachedData.items()
            if not k.startswith("data:")
            and k != USER_PLACEHOLDERS_CACHE_KEY
            and isinstance(v, dict)
        ]

    # To track if any primary action was handled
    actionTakenHandledByIfElse = False

    if parsedArgs.set_placeholder:
        actionTakenHandledByIfElse = True
        key, value = parsedArgs.set_placeholder

        if key not in CACHABLE_PLACEHOLDER_KEYS:
            console.print(
                f"[bold red]Error:[/bold red] Invalid key '{key}'. Must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}"
            )

            return 1
        userPlaceholdersCache[key] = value
        _cacheModifiedByAction = True
        console.print(
            f"Placeholder [green]'{key}'[/green] set to [cyan]'{value}'[/cyan]."
        )

    elif parsedArgs.get_placeholder:
        actionTakenHandledByIfElse = True
        key_to_get = parsedArgs.get_placeholder

        if key_to_get == "ALL_KEYS":
            console.print("\n[bold]Saved Placeholders:[/bold]")

            if not userPlaceholdersCache:
                console.print("  [dim](None saved)[/dim]")

            else:

                for k, v_val in userPlaceholdersCache.items():
                    console.print(f'  - [green]{k}[/green]: [cyan]"{v_val}"[/cyan]')

        elif key_to_get in CACHABLE_PLACEHOLDER_KEYS:
            value = userPlaceholdersCache.get(key_to_get)

            if value is not None:
                console.print(f'[green]{key_to_get}[/green]: [cyan]"{value}"[/cyan]')

            else:
                console.print(f"Placeholder [yellow]'{key_to_get}'[/yellow] not set.")

        else:
            console.print(
                f"[bold red]Error:[/bold red] Invalid key '{key_to_get}'. Must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)} or omit for all."
            )

            return 1

    elif (
        parsedArgs.clear_placeholders is not None
    ):  # nargs="*" means it's a list, empty if no args given
        actionTakenHandledByIfElse = True
        keys_to_clear = parsedArgs.clear_placeholders

        if not keys_to_clear:  # Clear all
            if not userPlaceholdersCache:
                console.print("No placeholders to clear.")

            else:
                userPlaceholdersCache.clear()
                _cacheModifiedByAction = True
                console.print("All saved placeholders cleared.")

        else:  # Clear specific keys
            cleared_any = False

            for k_clear in keys_to_clear:

                if k_clear in CACHABLE_PLACEHOLDER_KEYS:

                    if k_clear in userPlaceholdersCache:
                        del userPlaceholdersCache[k_clear]
                        _cacheModifiedByAction = True
                        cleared_any = True
                        console.print(
                            f"Placeholder [green]'{k_clear}'[/green] cleared."
                        )

                    else:
                        console.print(
                            f"Placeholder [yellow]'{k_clear}'[/yellow] was not set."
                        )

                else:
                    console.print(
                        f"[bold red]Error:[/bold red] Invalid key '{k_clear}' to clear. Must be one of: {', '.join(CACHABLE_PLACEHOLDER_KEYS)}"
                    )
            if not cleared_any and all(
                k not in CACHABLE_PLACEHOLDER_KEYS for k in keys_to_clear
            ):

                return 1  # Error if all provided keys were invalid

    elif parsedArgs.list is not None:
        actionTakenHandledByIfElse = True
        all_license_keys = GetAllLicenseCacheKeys(licensesData)
        target_keys = (
            [key.lower() for key in parsedArgs.list]
            if parsedArgs.list
            else all_license_keys
        )
        valid_target_keys = [key for key in target_keys if key in all_license_keys]
        ListLicenses(licensesData, valid_target_keys)

    elif parsedArgs.detailed_list is not None:
        actionTakenHandledByIfElse = True
        all_license_keys = GetAllLicenseCacheKeys(licensesData)
        target_keys = (
            [key.lower() for key in parsedArgs.detailed_list]
            if parsedArgs.detailed_list
            else all_license_keys
        )
        valid_target_keys = [key for key in target_keys if key in all_license_keys]
        PrintDetailedList(licensesData, valid_target_keys)

    elif parsedArgs.find:
        actionTakenHandledByIfElse = True
        FindLicenses(parsedArgs.require, parsedArgs.disallow, licensesData)

    elif parsedArgs.info:
        actionTakenHandledByIfElse = True
        reqIdLower = parsedArgs.info.lower()

        if (
            not licensesData.get(reqIdLower)
            or reqIdLower.startswith("data:")
            or reqIdLower == USER_PLACEHOLDERS_CACHE_KEY
        ):
            console.print(
                f"\n[bold red]Error:[/bold red] License '{parsedArgs.info}' not found."
            )

            return 1
        # Will use GetFullLicenseData internally
        DisplayLicenseInfo(reqIdLower, licensesData)

    elif parsedArgs.show_placeholders:
        actionTakenHandledByIfElse = True
        reqIdLower = parsedArgs.show_placeholders.lower()
        # Use GetFullLicenseData
        licenseEntry = GetFullLicenseData(reqIdLower, licensesData)

        if not licenseEntry:

            if not licensesData.get(reqIdLower):
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{parsedArgs.show_placeholders}' not found."
                )

            return 1

        console.print(
            f"\n[bold]--- Placeholders for {licenseEntry.get('title','N/A')} ({licenseEntry.get('spdx_id','N/A')}) ---[/bold]"
        )
        # From rich cache
        placeholdersInBody = licenseEntry.get("placeholders_in_body", [])
        fieldsDataList = licensesData.get("data:fields.yml", {}).get("content", [])
        fieldsData = {
            item["name"].lower(): item
            for item in fieldsDataList
            if isinstance(item, dict) and item.get("name")
        }

        if not placeholdersInBody:
            console.print("  [dim](No standard [placeholder] patterns found)[/dim]")

        else:

            for phFullStr in placeholdersInBody:
                phNoBrackets = phFullStr.strip("[]")
                phLower = phNoBrackets.lower()
                fieldInfo = fieldsData.get(phLower)
                desc = (
                    fieldInfo.get("description", "No desc.")
                    if fieldInfo
                    else "No desc."
                )
                argSugg = PLACEHOLDER_TO_ARG_MAP.get(
                    phLower, f"(no arg for '{phFullStr}')"
                )
                defInfo = (
                    " (defaults to current year)" if phLower in ["year", "yyyy"] else ""
                )
                console.print(
                    f"  - [bold magenta]{phFullStr}[/bold magenta]\n    [dim]Description:[/dim] {desc}\n    [dim]Argument:[/dim] {argSugg}{defInfo}"
                )

    elif parsedArgs.compare is not None:
        actionTakenHandledByIfElse = True
        all_license_keys = GetAllLicenseCacheKeys(licensesData)
        target_keys = (
            [key.lower() for key in parsedArgs.compare]
            if parsedArgs.compare
            else all_license_keys
        )
        valid_target_keys = [key for key in target_keys if key in all_license_keys]
        CompareLicenses(valid_target_keys, licensesData)

    elif parsedArgs.license:
        actionTakenHandledByIfElse = True
        reqIdLower = parsedArgs.license.lower()
        # Use GetFullLicenseData
        licenseEntry = GetFullLicenseData(reqIdLower, licensesData)

        if not licenseEntry:

            if not licensesData.get(reqIdLower):
                console.print(
                    f"\n[bold red]Error:[/bold red] License '{parsedArgs.license}' not found."
                )

            return 1

        console.print(
            f"\nUsing license: [bold cyan]{licenseEntry.get('title','N/A')}[/bold cyan] ({licenseEntry.get('spdx_id','N/A')})"
        )

        templateBody = licenseEntry.get("file_content_cached")
        # Should be caught by GetFullLicenseData, but double check
        if not isinstance(templateBody, str):
            console.print(
                f"[bold red]Error:[/bold red] License template body for {reqIdLower} is missing or invalid in cache."
            )

            return 1

        cachedPhAtStart = userPlaceholdersCache.copy()
        # For cachable keys
        userProvidedForCaching = {}

        if parsedArgs.fullname is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["fullname"]] = (
                parsedArgs.fullname
            )

        if parsedArgs.project is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["project"]] = parsedArgs.project

        if parsedArgs.email is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["email"]] = parsedArgs.email

        if parsedArgs.projecturl is not None:
            userProvidedForCaching[CLI_ARG_TO_CACHE_KEY["projecturl"]] = (
                parsedArgs.projecturl
            )

        # Start with cache
        finalReplacements = cachedPhAtStart.copy()
        # Override with CLI
        finalReplacements.update(userProvidedForCaching)
        finalReplacements["year"] = (
            parsedArgs.year if parsedArgs.year is not None else str(datetime.now().year)
        )
        # Ensure related keys like 'yyyy' also get this year value for filling
        finalReplacements["yyyy"] = finalReplacements["year"]

        if "fullname" in finalReplacements:
            # Ensure fullname variations are covered for filling
            finalReplacements["name of copyright owner"] = finalReplacements["fullname"]
            finalReplacements["login"] = finalReplacements["fullname"]

        userProvidedForSummary = userProvidedForCaching.copy()
        # Year actually used
        userProvidedForSummary["year"] = finalReplacements["year"]

        filledLicense = FillLicenseTemplate(templateBody, finalReplacements)
        outputPath = Path(parsedArgs.output) if parsedArgs.output else Path("LICENSE")

        try:
            outputPath.write_text(filledLicense + "\n", encoding="utf-8")

        except IOError as e:
            console.print(f"\n[bold red]Error:[/bold red] writing '{outputPath}': {e}")

            return 1

        if userProvidedForCaching:
            userPlaceholdersCache.update(userProvidedForCaching)
            _cacheModifiedByAction = True
            VerbosePrint("Updated saved placeholder preferences.")

        DisplayLicenseSummaryAfterWrite(
            licenseEntry,
            licensesData,
            outputPath,
            userProvidedForSummary,
            cachedPhAtStart,
            filledLicense,
        )

    if cacheUpdatedByFetch or _cacheModifiedByAction:
        SaveCache(cacheFilePath, licensesData)
    elif _verbose:
        console.print("No changes to save to cache file.")

    return 0


if __name__ == "__main__":

    sys.exit(main())
