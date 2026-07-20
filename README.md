# MSPM: Minecraft Package Manager

MSPM is a command-line utility designed to automate the resolution, installation, and management of Minecraft server
plugins. It retrieves packages from multiple popular upstream repositories, verifies software compatibility constraints,
and downloads updates directly to the target environment.

## Features

- Resolution across multiple platforms: Modrinth, Hangar, SpigotMC, and Bukkit.
- Support for direct URLs and GitHub releases.
- Automated compatibility verification based on server platform and target Minecraft version.
- Concurrency control for browser-based download flows requiring Cloudflare verification.
- Structured lockfiles (`mspm.toml.lock`) ensuring repeatable environments.

## Installation

To install the package directly from PyPI, run:

```bash
pip install mspm
```

## Configuration

When run for the first time, MSPM generates a template configuration file named `mspm.toml` in your working directory.
You can edit this file to match your server environment:

```toml
[server]
version = "1.20.4"
platform = "PAPER"
plugins_dir = "./plugins"
jar_name = "server.jar"

[dependencies]
# Your managed plugins will be added here
```

## Command Usage

### Search for Plugins

To search for a plugin across all indexed upstream repositories:

```bash
mspm search EssentialsX
```

### Add a Plugin

To register and download a plugin, add it by name:

```bash
mspm add EssentialsX
```

To specify a target source or version manually:

```bash
mspm add Vault --source modrinth --version 1.7.3
```

### Install Configured Dependencies

To install all plugins registered inside `mspm.toml` that are missing or require installation:

```bash
mspm install
```

### Update Managed Plugins

To query upstreams for new releases matching your server version and update existing files:

```bash
mspm update
```

### Remove Plugins

To delete a plugin and its tracked files from both configuration and server directory:

```bash
mspm remove EssentialsX
```

### General Arguments

- `--yes`, `-y`: Bypass confirmation prompts.
- `--allow-untested`: Force installation of components marked as incompatible.
- `--debug`: Run with verbose diagnostic output.

## License

This software is released under the MIT License.

Author: Nikita Denissov
