# GetLicense

A command-line tool, written in Rust and licensed under the GPLv3, to fetch, display info for, compare, find, and fill open source license templates from the [github/choosealicense.com](https://github.com/github/choosealicense.com) repository. It utilizes local caching via Git SHAs to minimize GitHub API calls and enhance performance.

## Features

* **License Discovery & Caching:**
  * Fetches license templates and metadata (`rules.yml`, `fields.yml`) from `choosealicense.com`.
  * Maintains an efficient local JSON cache (`license_cache_rs.json`).
  * Automatically updates cache based on remote file changes (Git SHAs).
  * Pre-parses and caches license details (placeholders, rules, descriptions) for faster operations.
* **Listing & Comparison:**
  * `list [SPDX_ID ...]`: Display a simple list of available licenses (all or specified).
  * `detailed-list [SPDX_ID ...]`: Show a detailed summary including rule tags.
  * `compare [SPDX_ID ...]`: Compare key properties (permissions, conditions, limitations) of specified licenses (or all) in a table format.
* **Detailed Information:**
  * `info <SPDX_ID>`: View comprehensive information for a specific license, including description, rules with details, and placeholders.
  * `show-placeholders <SPDX_ID>`: List only the placeholders (like `[year]`, `[fullname]`) required by a specific license template, along with their descriptions.
* **Finding Licenses:**
  * `find --require <RULE_TAG> --disallow <RULE_TAG>`: Search for licenses based on required or disallowed rule criteria (e.g., `commercial-use`, `disclose-source`).
* **Template Filling:**
  * `license <SPDX_ID> [options...]`: Generate a license file by filling placeholders (e.g., `--year`, `--fullname`, `--project`) in the chosen template.
  * Outputs a summary indicating the source of filled values (CLI argument, saved preference, default) and warns about any unfilled placeholders remaining in the output file.
* **Placeholder Preferences:**
  * Manage saved default values for common placeholders (`fullname`, `project`, `email`, `projecturl`) to streamline license generation.
  * Commands: `set-placeholder`, `get-placeholder`, `clear-placeholders`.
* **Shell Completion:**
  * Generate shell completion scripts (`--generate-completion <SHELL>`) for common shells (Zsh, Bash, Fish, etc.).

## Installation

### Prerequisites

* Rust toolchain (latest stable recommended). Install via [rustup.rs](https://rustup.rs/).

### From Source

1. Clone the repository:

    ```bash
    git clone https://github.com/kgruiz/getlicense.git
    cd getlicense
    ```

2. Build the release binary:

    ```bash
    cargo build --release
    ```

### From Crates.io (Once Published)

```bash
cargo install getlicense
```

### Automated Setup

After building the release binary:

```bash
./install.sh
```

This script installs the `getlicense` binary, sets up shell completion for your default shell, and updates your shell config.

**Optional flags:**

| Flag         | Description                                    |
|--------------|------------------------------------------------|
| `--shell`    | Specify shell (bash, zsh, fish, powershell, nushell, elvish, fig) |
| `--force`    | Overwrite existing files without prompting     |
| `--dry-run`  | Preview steps without making changes           |
| `--reload`   | Prompt to reload your shell config after setup|
| `--bin-dir`  | Override binary install path                   |
| `--src-bin`  | Path to prebuilt binary                        |

### Manual Setup

Copy the binary to a directory in your `PATH`, generate completion, and update your shell config manually.

**Zsh:**

```bash
mkdir -p ~/.local/bin
cp target/release/getlicense ~/.local/bin/getlicense
export PATH="$HOME/.local/bin:$PATH"

mkdir -p ~/.zsh/completion
getlicense --generate-completion zsh > ~/.zsh/completion/_getlicense

# In ~/.zshrc, add:
fpath=(~/.zsh/completion $fpath)
autoload -Uz compinit && compinit
```

**Bash:**

```bash
mkdir -p ~/.local/bin
cp target/release/getlicense ~/.local/bin/getlicense
export PATH="$HOME/.local/bin:$PATH"

mkdir -p ~/.bash_completion.d
getlicense --generate-completion bash > ~/.bash_completion.d/getlicense

# In ~/.bashrc, add:
source ~/.bash_completion.d/getlicense
```

## Usage

For a full list of commands and options:

```bash
getlicense --help
```

**Common Examples:**

```bash
# List all available licenses (uses cache if available)
getlicense list

# Force refresh cache then list licenses
getlicense --refresh list

# Show detailed info for the MIT license
getlicense info MIT

# Show placeholders required by the Apache-2.0 license
getlicense show-placeholders Apache-2.0

# Compare MIT, Apache-2.0, and GPL-3.0 licenses
getlicense compare MIT Apache-2.0 GPL-3.0

# Find licenses permitting commercial use but requiring source disclosure
getlicense find --require commercial-use --require disclose-source

# Generate an MIT license file named 'LICENSE_MIT', filling placeholders
getlicense license MIT --fullname "Example Corp." --year 2024 --project "My Project" -o LICENSE_MIT

# Save a default value for the 'fullname' placeholder for future use
getlicense set-placeholder fullname "My Default Name/Org"

# View all saved placeholder preferences
getlicense get-placeholder

# Clear only the saved 'email' preference
getlicense clear-placeholders email
```

### Shell Completion Setup

Generate the completion script for your preferred shell and follow its installation instructions.

**Example for Zsh:**

```bash
getlicense --generate-completion zsh > _getlicense
# Move the generated _getlicense file to a directory in your $fpath
# (e.g., ~/.zsh/completion/, creating it if necessary)
mkdir -p ~/.zsh/completion
mv _getlicense ~/.zsh/completion/
# Ensure your .zshrc sources completions, typically includes lines like:
# fpath=($HOME/.zsh/completion $fpath)
# autoload -Uz compinit && compinit
```

**Example for Bash:**

```bash
getlicense --generate-completion bash > getlicense-completion.bash
# Source the file from your .bashrc or .bash_profile:
# echo 'source ~/getlicense-completion.bash' >> ~/.bashrc
# Alternatively, if using bash-completion package:
# mkdir -p ~/.local/share/bash-completion/completions
# mv getlicense-completion.bash ~/.local/share/bash-completion/completions/getlicense
```

## Development

Standard Rust project commands:

* Build: `cargo build`
* Run: `cargo run -- <args>`
* Test: `cargo test`
* Format: `cargo fmt`
* Lint: `cargo clippy`

## Contributing

Contributions are welcome! Please feel free to open an issue to discuss changes or submit a pull request. Note that contributions to this project are accepted under the terms of the GPLv3 license.

## License

**Tool License:**

This project (the `getlicense` tool itself) is licensed under the **GNU General Public License version 3 (GPLv3)**.

* The full text of the license is included in the [LICENSE](./LICENSE) file in this repository.

**Content Source Licensing:**

The license templates and metadata fetched by this tool originate from the [github/choosealicense.com](https://github.com/github/choosealicense.com) repository. That content is separately licensed:

* The *content* (license texts, descriptions, metadata) is licensed under the [Creative Commons Attribution 3.0 Unported license (CC BY 3.0)](https://creativecommons.org/licenses/by/3.0/).
* The *source code* of the choosealicense.com website itself is licensed under the MIT license.

This tool's GPLv3 license applies only to the code of the tool itself, not to the content it fetches and displays. Proper attribution to the source is provided by this notice.
