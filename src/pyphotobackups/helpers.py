from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image
from tqdm import tqdm


class Abort(Exception):
    pass


# Lock File Management
def create_lock_file(dir: Path) -> None:
    """
    Create a lock file to ensure there is only one process running.
    """
    lock_file = dir / "pyphotobackups.lock"
    lock_file.touch()


def is_lock_file_exists(dir: Path) -> bool:
    lock_file = dir / "pyphotobackups.lock"
    if lock_file.exists():
        return True
    return False


def cleanup_lock_file(dir: Path):
    lock_file = dir / "pyphotobackups.lock"
    if lock_file.exists():
        lock_file.unlink()


# Database Management
def get_db_path(target_dir: Path) -> Path:
    """
    This function defines the path of the db file to be stored, under the dest dir.
    """
    backup_dir = target_dir / ".pyphotobackups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir / "db"


def init_db(target_dir: Path) -> sqlite3.Connection:
    """
    Initialize the database and return the connection.

    This functions also creates two tables, `run` and `sync`, if they did not exist.
    """
    conn = sqlite3.connect(get_db_path(target_dir))
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sync (
            source TEXT PRIMARY KEY,
            dest TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            inserted_at TIMESTAMP NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS run (
            id TEXT PRIMARY KEY,
            serial_number TEXT NOT NULL,
            dest TEXT NOT NULL,
            start TIMESTAMP NOT NULL,
            end TIMESTAMP NOT NULL,
            elapsed_time TEXT NOT NULL,
            dest_size TEXT NOT NULL,
            dest_size_increment TEXT NOT NULL,
            new_sync INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    cursor.close()
    return conn


def is_processed_source(source: str, conn: sqlite3.Connection) -> bool:
    """
    Check if a file from source has already been processed by its path (as in format `100APPLE/IMAGE_001.png` etc.)
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sync WHERE source = ?", (source,))
    count = cursor.fetchone()[0]
    cursor.close()
    return count > 0


# iPhone connection
def is_ifuse_installed() -> bool:
    if shutil.which("ifuse"):
        return True
    return False


def is_iPhone_mounted() -> bool:
    with open("/proc/mounts", "r") as mounts:
        for line in mounts:
            if "ifuse" in line:
                return True
    return False


def mount_iPhone(mount_point: Path) -> None:
    if is_iPhone_mounted():
        raise Abort("iPhone is already mounted")
    mount_point.mkdir(parents=True, exist_ok=True)
    run = subprocess.run(
        ["ifuse", str(mount_point)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if run.returncode == 1:
        mount_point.rmdir()
        raise Abort("iPhone is not connected")


def unmount_iPhone(mount_point: Path) -> None:
    subprocess.run(["umount", str(mount_point)])
    mount_point.rmdir()


def get_serial_number() -> str:
    """
    Retrieve the serial number from a mounted iPhone.
    """
    result = subprocess.run(
        ["ideviceinfo", "-k", "SerialNumber"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


# Directory and File Operations
def get_directory_size(path: Path) -> int:
    total_size = 0
    for file in path.rglob("*"):
        if file.is_file():
            total_size += file.stat().st_size
    return total_size


def get_photo_creation_time(path: Path) -> datetime | None:
    """
    Extract the DateTimeOriginal EXIF datetime from image metadata.
    """
    DATETIME_ORIGINAL_TAG = 36867
    try:
        exif = Image.open(path).getexif()
        if DATETIME_ORIGINAL_TAG in exif:
            raw_date = exif[DATETIME_ORIGINAL_TAG]
            return datetime.strptime(raw_date, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def get_video_creation_time(path: Path) -> datetime | None:
    """
    Extract creation timestamp from video metadata using ffprobe.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_entries",
        "format_tags=creation_time",
        str(path),
    ]
    try:
        output = subprocess.check_output(cmd, text=True)
        tags = json.loads(output).get("format", {}).get("tags", {})
        creation_time = tags.get("creation_time")
        if creation_time:
            return datetime.fromisoformat(creation_time.replace("Z", "+00:00"))
    except (subprocess.CalledProcessError, KeyError, ValueError):
        pass
    return None


def get_file_timestamp(path: Path) -> datetime:
    """
    Get the most accurate timestamp for a file, preferring metadata over filesystem times.
    """
    ext = path.suffix.lower()
    timestamp = None

    if ext in {".jpg", ".jpeg", ".heic", ".png"}:
        timestamp = get_photo_creation_time(path)
    elif ext in {".mp4", ".mov", ".3gp"}:
        timestamp = get_video_creation_time(path)

    # Fallback to last modified time if metadata fails
    if timestamp is None:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime)
    return timestamp


def convert_size_to_readable(size: int) -> str:
    """
    Convert a size in bytes to a human-readable format (e.g., KB, MB, GB).

    Args:
        size (int): The size in bytes.

    Returns:
        str: The size in a human-readable format (e.g., "1.0K", "2.3M").
    """
    num = float(size)
    if num == 0:
        return "0B"
    for unit in ["B", "K", "M", "G"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}T"


def process_dir_recursively(
    source_dir: Path,
    target_dir: Path,
    conn: sqlite3.Connection,
    counter: int,
    size_increment: int,
) -> tuple[int, int, int]:
    """
    Recursively processes files from the source directory, copying them to the target directory
    while updating a file sync database. Tracks the number of files copied and the total size.

    Args:
        - source_dir (Path): The directory to process.
        - target_dir (Path): The destination directory for the files.
        - conn (sqlite3.Connection): The database connection for file sync tracking.
        - counter (int): The number of files processed, updated during recursion.
        - size_increment (int): The total size of processed files, updated during recursion.

    Returns:
        tuple[int, int, int]:
            - exit_code (int): 1 if interrupted, 0 if successful.
            - counter (int): Updated file count.
            - size_increment (int): Updated total file size in bytes.

    Notes:
        - Copies files to a subdirectory based on their timestamp.
        - If file with the same name exists, an increment suffix will be appended.
        - Skips already processed files and handles errors like permission issues or insufficient space.
        - Stops and returns if interrupted (via KeyboardInterrupt).
    """
    try:
        dirs = [path for path in source_dir.iterdir() if path.is_dir()]
        dirs = sorted(dirs)
        files = [path for path in source_dir.iterdir() if path.is_file()]
        exit_code = 0

        # depth first
        for dir in dirs:
            exit_code, counter, size_increment = process_dir_recursively(
                dir, target_dir, conn, counter, size_increment
            )
            if exit_code == 1:
                return exit_code, counter, size_increment

        if not files:
            return exit_code, counter, size_increment
        for file_path in tqdm(
            files,
            desc=f"syncing : {source_dir.name:<18} |",
            bar_format="{desc} {bar} [{n_fmt}/{total_fmt}]",
            ncols=80,
            miniters=1,
        ):
            source = str(Path(*file_path.parts[-2:]))
            if is_processed_source(source, conn):
                continue
            file_name = file_path.name
            file_timestamp = get_file_timestamp(file_path)
            year_month = file_timestamp.strftime("%Y-%m")
            target_subdir = target_dir / year_month
            target_subdir.mkdir(parents=True, exist_ok=True)
            target_file_path = target_subdir / file_name
            # Ensure unique file name by incrementing if a duplicate exists
            duplicates = 1
            while target_file_path.exists():
                stem, suffix = file_name.rsplit(".", 1)
                file_name = f"{stem}_{duplicates}.{suffix}"
                target_file_path = target_subdir / file_name
                duplicates += 1
            target_file_path_tmp = target_subdir / f"{file_name}.tmp"

            try:
                # prevents incomplete files by copying to a temporary file first
                shutil.copy2(file_path, target_file_path_tmp)
                os.replace(target_file_path_tmp, target_file_path)
            except OSError as e:
                if e.errno == errno.EACCES:
                    print("[pyphotobackups] permission denied")
                    raise Abort
                if e.errno == errno.ENOSPC:
                    print("[pyphotobackups] no enough space in destination directory")
                    raise Abort
                raise e

            counter += 1
            size_increment += file_path.stat().st_size
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO sync (source, dest, timestamp, inserted_at) VALUES (?, ?, ?, ?)",
                (
                    source,
                    str(Path(*target_file_path.parts[-2:])),
                    file_timestamp,
                    datetime.now(),
                ),
            )
            conn.commit()
            cursor.close()
    except KeyboardInterrupt:
        print("[pyphotobackups] interrupted! saving current progress...")
        exit_code = 1
    return exit_code, counter, size_increment
