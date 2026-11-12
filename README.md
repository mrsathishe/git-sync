# git-sync

A lightweight local dashboard for monitoring and syncing your GitHub repositories from one place.

This project uses the GitHub CLI (`gh`), Git, and a local web interface to:

- list repositories available to your GitHub account
- clone missing repositories automatically
- fetch updates for tracked repositories
- detect local changes and sync status
- keep a recovery folder for deleted repositories
- expose everything through a dashboard at http://127.0.0.1:8765

## Overview

Managing many repositories by hand can become time-consuming and error-prone. `git-sync` helps keep your local workspace aligned with GitHub by centralizing repo inventory, health checks, and sync operations in a single dashboard.

It is useful for developers who maintain multiple repositories and want to:

- see which repositories are clean or dirty
- identify repositories that are ahead or behind their remote branch
- sync a large local workspace without opening each repo individually
- protect deleted repositories by moving them into a recovery area instead of deleting them immediately

## Features

- GitHub inventory sync via `gh api /user/repos`
- automatic cloning of new repositories
- fetching updates for existing repositories
- detection of modified, untracked, and deleted files
- tracking of repos ahead of or behind their remote
- simple browser-based dashboard
- safe recovery handling for repositories removed from GitHub

## Project structure

- `server.py` — main HTTP server and sync logic
- `index.html` — dashboard UI
- `manifest.json` — tracked repository metadata
- `logs/` — runtime logs
- `git_manager.py` — Git helper logic
- `github_client.py` — GitHub API helper logic
- `sync.py` — sync helper entry point

## Requirements

Before running the project, make sure you have:

- Python 3.9+
- Git
- GitHub CLI (`gh`)
- a valid GitHub account configured with `gh auth login`

Verify authentication:

```bash
gh auth status