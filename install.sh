#!/usr/bin/env sh

# install.sh — install getlicense + shell completion (bash|zsh|fish|powershell|nushell|elvish|fig)

BIN_DIR="${HOME}/.local/bin"
SRC_BIN="target/release/getlicense"
FORCE=0
DRY_RUN=0
OPT_SHELL=""
RELOAD=0

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --shell SHELL      Choose shell: bash, zsh, fish, powershell, nushell, elvish, fig
                     (default: your \$SHELL basename)
  --bin-dir DIR      Install binary to DIR (default: ~/.local/bin)
  --src-bin PATH     Path to compiled binary (default: target/release/getlicense)
  --force            Skip prompts, overwrite without asking
  --dry-run          Print actions without making changes
  --reload           Prompt to reload shell config after install
  -h, --help         Show this help
EOF
    exit 1
}

# parse args
while [ $# -gt 0 ]; do
    case "$1" in
    --shell)
        OPT_SHELL="$2"
        shift 2
        ;;
    --bin-dir)
        BIN_DIR="$2"
        shift 2
        ;;
    --src-bin)
        SRC_BIN="$2"
        shift 2
        ;;
    --force)
        FORCE=1
        shift
        ;;
    --dry-run)
        DRY_RUN=1
        shift
        ;;
    --reload)
        RELOAD=1
        shift
        ;;
    -h | --help) usage ;;
    *)
        printf 'Error: unknown argument: %s\n' "$1" >&2
        usage
        ;;
    esac
done

# validate BIN_DIR
case "$BIN_DIR" in "$HOME"/*) ;; *)
    printf 'Error: %s must be under \$HOME\n' "$BIN_DIR" >&2
    exit 1
    ;;
esac

# always rebuild
if command -v cargo >/dev/null 2>&1; then
    printf 'Building getlicense...\n'
    cargo build --release || {
        printf 'Error: build failed\n' >&2
        exit 1
    }
    printf 'Build complete\n'
fi

# install binary
if command -v getlicense >/dev/null 2>&1; then
    EXIST=$(command -v getlicense)
    EV=$($EXIST --version 2>/dev/null)
    NV=$($SRC_BIN --version 2>/dev/null)
    printf 'Existing getlicense: %s (version %s)\n' "$EXIST" "$EV"
    if [ "$EV" != "$NV" ] && [ $FORCE -eq 0 ]; then
        printf 'Overwrite with version %s? [y/N] ' "$NV"
        read ans
        echo
        case "$ans" in [Yy]*) ;; *) SKIP_COPY=1 ;; esac
    fi
fi

if [ -z "$SKIP_COPY" ]; then
    if [ $DRY_RUN -eq 1 ]; then
        printf 'DRY RUN: mkdir -p %s && cp %s %s/getlicense\n' "$BIN_DIR" "$SRC_BIN" "$BIN_DIR"
    else
        printf 'Installing binary to %s...\n' "$BIN_DIR"
        mkdir -p "$BIN_DIR" && cp "$SRC_BIN" "$BIN_DIR/getlicense"
        printf 'Installed binary to %s\n' "$BIN_DIR"
    fi
fi

# determine shell
if [ -n "$OPT_SHELL" ]; then
    shell="$OPT_SHELL"
else
    shell=$(basename "$SHELL")
fi
case "$shell" in
pwsh) shell="powershell" ;;
powershell | bash | zsh | fish | nushell | elvish | fig) ;;
*)
    printf 'Error: unsupported shell: %s\n' "$shell" >&2
    exit 1
    ;;
esac

# ensure shell installed
if [ "$shell" != "fig" ] && ! command -v "$shell" >/dev/null 2>&1; then
    printf 'Error: %s not found. Install %s and retry.\n' "$shell" "$shell" >&2
    exit 1
fi

# setup per-shell vars
case "$shell" in
bash)
    COMP_DIR="$HOME/.bash_completion.d"
    COMP_FILE="$COMP_DIR/getlicense"
    RC_FILE="$HOME/.bashrc"
    LOAD_LINE="source $COMP_FILE"
    ;;
zsh)
    COMP_DIR="$HOME/.zsh/completion"
    COMP_FILE="$COMP_DIR/_getlicense"
    RC_FILE="$HOME/.zshrc"
    LOAD_BLOCK=$(printf '\n# getlicense completion\nfpath=(%s $fpath)\nautoload -Uz compinit && compinit\n' "$COMP_DIR")
    ;;
fish)
    COMP_DIR="$HOME/.config/fish/completions"
    COMP_FILE="$COMP_DIR/getlicense.fish"
    RC_FILE=""
    ;;
powershell)
    COMP_DIR="$HOME/.config/powershell"
    COMP_FILE="$COMP_DIR/getlicense.ps1"
    RC_FILE="$HOME/.config/powershell/Microsoft.PowerShell_profile.ps1"
    LOAD_LINE=". '$COMP_FILE'"
    ;;
nushell)
    COMP_DIR="$HOME/.config/nushell/completions"
    COMP_FILE="$COMP_DIR/getlicense.nu"
    RC_FILE=""
    ;;
elvish)
    COMP_DIR="$HOME/.config/elvish/completions"
    COMP_FILE="$COMP_DIR/getlicense.elv"
    RC_FILE=""
    ;;
fig)
    COMP_DIR="$HOME/.fig/autocomplete"
    COMP_FILE="$COMP_DIR/getlicense.sh"
    RC_FILE=""
    ;;
esac

# generate completion
if [ $DRY_RUN -eq 1 ]; then
    printf 'DRY RUN: mkdir -p %s\n' "$COMP_DIR"
else
    mkdir -p "$COMP_DIR"
fi

if [ $DRY_RUN -eq 1 ]; then
    printf 'DRY RUN: getlicense --generate-completion %s > %s\n' "$shell" "$COMP_FILE"
else
    getlicense --generate-completion "$shell" >"$COMP_FILE"
    printf 'Wrote completion to %s\n' "$COMP_FILE"
fi

# modify rc file if needed
if [ -n "$RC_FILE" ] && [ -w "$RC_FILE" ]; then
    if [ "$shell" = zsh ]; then
        if ! grep -Fq 'compinit' "$RC_FILE"; then
            if [ $DRY_RUN -eq 1 ]; then
                printf 'DRY RUN: append to %s: %s' "$RC_FILE" "$LOAD_BLOCK"
            else
                printf '%s' "$LOAD_BLOCK" >>"$RC_FILE"
                printf 'Updated %s\n' "$RC_FILE"
            fi
        fi
    else
        if ! grep -Fq "$LOAD_LINE" "$RC_FILE"; then
            if [ $DRY_RUN -eq 1 ]; then
                printf 'DRY RUN: append to %s: %s\n' "$RC_FILE" "$LOAD_LINE"
            else
                printf '\n# getlicense completion\n%s\n' "$LOAD_LINE" >>"$RC_FILE"
                printf 'Updated %s\n' "$RC_FILE"
            fi
        fi
    fi
fi

# reload if requested
if [ $RELOAD -eq 1 ] && [ -t 0 ]; then
    if [ -n "$RC_FILE" ]; then
        printf 'Reload now? [y/N] '
        read ans
        echo
        case "$ans" in
        [Yy]*)
            if [ "$shell" = "powershell" ]; then
                pwsh -NoExit -Command "Invoke-Expression -Command '. $RC_FILE'"
            else
                # assume POSIX
                . "$RC_FILE"
            fi
            printf 'Reloaded %s\n' "$RC_FILE"
            ;;
        esac
    fi
fi

# final message
case "$shell" in
bash | zsh)
    printf 'To apply changes manually: source %s\n' "$RC_FILE"
    ;;
fish)
    printf 'No reload needed (fish autoloads completions).\n'
    ;;
powershell)
    printf 'Restart PowerShell or run: %s\n' "$RC_FILE"
    ;;
nushell)
    printf 'Restart nushell to load completions.\n'
    ;;
elvish)
    printf 'Restart elvish to load completions.\n'
    ;;
fig)
    printf 'Fig will pick up the new completion automatically.\n'
    ;;
esac

printf 'Done.\n'
