# CS2 Value

Public update channel for the CS2 Value project.

## Update model

The installed Windows application keeps its database, API keys, Python environment and logs locally. GitHub is used only as a public update channel.

Starting from the v0.22.4 GitHub-bootstrap build, the application can update itself from menu item `0`.

Updates are distributed as a verified patch chain:

- `latest.json` declares the latest version and safe version-to-version path;
- each release manifest lists only changed files;
- every downloaded file is verified with SHA-256 before installation;
- changed local files and the SQLite database are backed up before applying a patch;
- dependencies, DB schema and automated tests are checked after an update;
- if validation fails, program files and the database are rolled back.

## Current baseline

- Version: v0.22.4
- Update schema: patch-chain v1
- Local `data/`, SQLite database, API keys, `.venv` and `logs/` are never published here.

Current project focus: CS2 probability modelling, map/veto analysis, Pinnacle value validation, then prematch/live integration and Telegram.
