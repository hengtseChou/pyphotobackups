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

> ⚠️ Note that for technical reasons related to filesystem manipulation, `pyphotobackups` is only available on Linux platforms right now.

## Dependency

You will need to have `ifuse` installed on your system.

## Usage

```
pyphotobackups <DESTINATION>
```

That's it!

## License

GPL-3.0
