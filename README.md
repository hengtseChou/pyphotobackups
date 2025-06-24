# pyphotobackups

![screenshot1](https://raw.githubusercontent.com/hengtseChou/pyphotobackups/refs/heads/main/.github/assets/screenshot1.png)

A very simple command line tool to sync photos and videos from your iPhone to your drive, and organize by the time you took them.

## Features

1. **Auto-Organize by Date:** Sorts photos and videos into YYYY-MM folders based on modification time.
2. **Incremental Backup:** Syncs only new files, speeding up backups.

## Installation

### with uv

```
uv tool install pyphotobackups
```

### with pipx

```
pipx install pyphotobackups
```

## Dependency

You will need to have `ifuse` installed on your system.

## Usage

```
pyphotobackups <DESTINATION>
```

That's it!

## Limitation

For technical reasons related to filesystem manipulation, pyphotobackups is currently limited to Linux platforms.

## License

GPL-3.0
