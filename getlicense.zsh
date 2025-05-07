# getlicense: fetch and customize an open-source license into a repository

typeset -A LICENSE_ALIAS LICENSE_DESC

LICENSE_ALIAS=(
  0bsd                0bsd
  afl-3.0             afl-3.0
  agpl-3.0            agpl-3.0
  apache-2.0          apache-2.0
  artistic-2.0        artistic-2.0
  blueoak-1.0.0       blueoak-1.0.0
  bsd-2-clause        bsd-2-clause
  bsd-2-clause-patent bsd-2-clause-patent
  bsd-3-clause        bsd-3-clause
  bsd-3-clause-clear  bsd-3-clause-clear
  bsd-4-clause        bsd-4-clause
  boost-1.0           boost-1.0
  cc0-1.0             cc0-1.0
  cc-by-4.0           cc-by-4.0
  cc-by-sa-4.0        cc-by-sa-4.0
  cc-by-nd-4.0        cc-by-nd-4.0
  cc-by-nc-4.0        cc-by-nc-4.0
  cc-by-nc-sa-4.0     cc-by-nc-sa-4.0
  cc-by-nc-nd-4.0     cc-by-nc-nd-4.0
  cecill-b            cecill-b
  cecill-c            cecill-c
  cecill-s            cecill-s
  ecl-2.0             ecl-2.0
  epl-1.0             epl-1.0
  epl-2.0             epl-2.0
  eupl-1.1            eupl-1.1
  eupl-1.2            eupl-1.2
  fdl-1.3             fdl-1.3
  gpl-2.0             gpl-2.0
  gpl-3.0             gpl-3.0
  isc                 isc
  lgpl-2.1            lgpl-2.1
  lgpl-3.0            lgpl-3.0
  lppl-1.3c           lppl-1.3c
  mit                 mit
  mit-0               mit-0
  mpl-2.0             mpl-2.0
  ms-pl               ms-pl
  ms-rl               ms-rl
  mulan-pl-2.0        mulan-pl-2.0
  ncsa                ncsa
  odbl-1.0            odbl-1.0
  ofl-1.1             ofl-1.1
  osl-3.0             osl-3.0
  postgresql          postgresql
  unlicense           unlicense
  upl-1.0             upl-1.0
  vim                 vim
  wtfpl               wtfpl
  zlib                zlib
)

LICENSE_DESC=(
  0bsd                "BSD Zero Clause License"
  afl-3.0             "Academic Free License v3.0"
  agpl-3.0            "GNU Affero GPL v3.0"
  apache-2.0          "Apache License 2.0"
  artistic-2.0        "Artistic License 2.0"
  blueoak-1.0.0       "Blue Oak Model License 1.0.0"
  bsd-2-clause        "BSD 2-Clause Simplified"
  bsd-2-clause-patent "BSD 2-Clause Plus Patent"
  bsd-3-clause        "BSD 3-Clause Revised"
  bsd-3-clause-clear  "BSD 3-Clause Clear"
  bsd-4-clause        "BSD 4-Clause Original"
  boost-1.0           "Boost Software License 1.0"
  cc0-1.0             "CC0 1.0 Universal"
  cc-by-4.0           "CC Attribution 4.0"
  cc-by-sa-4.0        "CC BY-SA 4.0"
  cc-by-nd-4.0        "CC BY-ND 4.0"
  cc-by-nc-4.0        "CC BY-NC 4.0"
  cc-by-nc-sa-4.0     "CC BY-NC-SA 4.0"
  cc-by-nc-nd-4.0     "CC BY-NC-ND 4.0"
  cecill-b            "CeCILL v2.1 Permissive"
  cecill-c            "CeCILL v2.1 Weakly Reciprocal"
  cecill-s            "CeCILL v2.1 Strongly Reciprocal"
  ecl-2.0             "Educational Community License 2.0"
  epl-1.0             "Eclipse Public License 1.0"
  epl-2.0             "Eclipse Public License 2.0"
  eupl-1.1            "EU Public License 1.1"
  eupl-1.2            "EU Public License 1.2"
  fdl-1.3             "GNU Free Documentation License 1.3"
  gpl-2.0             "GNU General Public License 2.0"
  gpl-3.0             "GNU General Public License 3.0"
  isc                 "ISC License"
  lgpl-2.1            "GNU LGPL 2.1"
  lgpl-3.0            "GNU LGPL 3.0"
  lppl-1.3c           "LaTeX Public License 1.3c"
  mit                 "MIT License"
  mit-0               "MIT No Attribution"
  mpl-2.0             "Mozilla Public License 2.0"
  ms-pl               "Microsoft Public License"
  ms-rl               "Microsoft Reciprocal License"
  mulan-pl-2.0        "Mulan Permissive Software License v2.0"
  ncsa                "NCSA Open Source License"
  odbl-1.0            "ODbL 1.0"
  ofl-1.1             "SIL Open Font License 1.1"
  osl-3.0             "Open Software License 3.0"
  postgresql          "PostgreSQL License"
  unlicense           "The Unlicense"
  upl-1.0             "Universal Permissive License 1.0"
  vim                 "Vim License"
  wtfpl               "Do What The F*ck You Want To Public License"
  zlib                "zlib License"
)

_print_help() {
  cat <<EOF
Usage: getlicense [options] LICENSE_ID

Options:
  -o <path>   Output path (default: ./LICENSE)
  -n <name>   Author name (git config user.name)
  -y <year>   Year (current year)
  -l          List licenses
  -f          Force overwrite
  -h          Help

Exit Codes:
  0 Success
  1 General error
  2 Invalid ID
  3 Fetch failure
  4 Write failure
EOF
}

_list_licenses() {
  local -a keys
  keys=("${(@k)LICENSE_DESC}")

  for id in "${keys[@]}"; do
    printf "%-20s %s\n" "$id" "${LICENSE_DESC[$id]}"
  done | sort
}

_fetch_template() {
  local id="$1"
  local cacheDir="$HOME/.cache/getlicense"
  local cacheFile="$cacheDir/$id.txt"

  mkdir -p "$cacheDir" || return 3

  if [[ -f $cacheFile ]]; then
    echo "$cacheFile"
    return 0
  fi

  curl -fsSL \
    "https://raw.githubusercontent.com/github/choosealicense.com/main/_licenses/$id.txt" \
    -o "$cacheFile" || return 3

  echo "$cacheFile"
}

getlicense() {
  local outputPath fullname year currentYear
  local flagList=false flagHelp=false flagForce=false
  local licenseId

  currentYear=$(date +%Y)

  while (( $# )); do
    case "$1" in
      --)           shift; break ;;
      -h|--help)    flagHelp=true    ;;
      -l|--list)    flagList=true    ;;
      -f|--force)   flagForce=true   ;;
      -o|--output)  shift; outputPath="$1" ;;
      --output=*)   outputPath="${1#*=}"    ;;
      -n|--name)    shift; fullname="$1"    ;;
      --name=*)     fullname="${1#*=}"      ;;
      -y|--year)    shift; year="$1"        ;;
      --year=*)     year="${1#*=}"          ;;
      -*)           echo "Error: Unknown option '$1'" >&2; return 1 ;;
      *)            licenseId="$1"; break   ;;
    esac

    shift
  done

  if [[ $flagHelp == true ]]; then
    _print_help
    return 0
  fi

  if [[ $flagList == true ]]; then
    _list_licenses
    return 0
  fi

  if [[ -z $licenseId ]]; then
    echo "Error: LICENSE_ID required" >&2
    return 1
  fi

  if [[ -n ${LICENSE_ALIAS[$licenseId]} ]]; then
    licenseId=${LICENSE_ALIAS[$licenseId]}
  fi

  if [[ -z ${LICENSE_DESC[$licenseId]} ]]; then
    echo "Error: Unknown license '$licenseId'" >&2
    return 2
  fi

  year=${year:-$currentYear}
  fullname=${fullname:-$(git config user.name 2>/dev/null || whoami)}
  outputPath=${outputPath:-./LICENSE}

  local templateFile content
  templateFile=$(_fetch_template "$licenseId") || return 3

  content=$(sed \
    -e "s/\[year\]/$year/g" \
    -e "s/\[fullname\]/$fullname/g" \
    "$templateFile"
  )

  if [[ -f $outputPath && $flagForce != true ]]; then
    read -q "?Overwrite $outputPath? [y/N] " yn
    echo
    [[ $yn =~ ^[Yy]$ ]] || return 1
  fi

  printf "%s\n" "$content" >| "$outputPath" || return 4
  chmod 644 "$outputPath" 2>/dev/null
}

# Zsh completion
if [[ -n $ZSH_VERSION ]]; then
  _getlicense() {
    local -a ids
    ids=("${(@k)LICENSE_ALIAS}")

    _arguments \
      '-o:output path:_files' \
      '-n:name:' \
      '-y:year:' \
      '-l[list licenses]' \
      '-f[force]' \
      '-h[help]' \
      '*:license:->license'

    if [[ $state == license ]]; then
      _describe 'licenses' ids
    fi
  }

  compdef _getlicense getlicense
fi