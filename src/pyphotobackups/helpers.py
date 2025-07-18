from __future__ import annotations

import errno
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import piexif
from PIL import Image
from pillow_heif import register_heif_opener
from tqdm import tqdm

register_heif_opener()


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
            exit_code INTEGER NOT NULL,
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


def is_unwanted_file(source: str):
    ext = source.split(".")[1].lower()
    if ext == "aae":
        return True
    False


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


def get_timestamp_by_png_metadata(path: Path) -> datetime | None:
    with open(path, "rb") as f:
        # Verify PNG signature
        signature = f.read(8)
        if signature != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a valid PNG file.")

        # Iterate over PNG chunks
        while True:
            length_bytes = f.read(4)
            if len(length_bytes) < 4:
                break  # EOF
            length = int.from_bytes(length_bytes, byteorder="big")
            chunk_type = f.read(4).decode("ascii")
            data = f.read(length)
            f.read(4)  # skip CRC

            if chunk_type == "eXIf":
                # Look for EXIF-style datetime pattern
                match = re.search(rb"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", data)
                if match:
                    datetime_str = match.group().decode()
                    return datetime.strptime(datetime_str, "%Y:%m:%d %H:%M:%S")
    return None


def get_timestamp_by_jpeg_metadata(path: Path) -> datetime | None:
    exif_dict = piexif.load(str(path))
    date_time_original = exif_dict["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
    if date_time_original:
        return datetime.strptime(date_time_original.decode("utf-8"), "%Y:%m:%d %H:%M:%S")
    return None


def get_timestamp_by_heic_metadata(path: Path) -> datetime | None:
    image = Image.open(path)
    exif_data = image.getexif()

    for tag_id, value in exif_data.items():
        tag = Image.ExifTags.TAGS.get(tag_id, tag_id)
        if tag == "DateTime":
            return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    return None


def get_timestamp_by_file_system(path: Path) -> datetime:
    timestamp = path.stat().st_mtime
    last_modified = datetime.fromtimestamp(timestamp)
    return last_modified


def get_timestamp(path: Path) -> datetime:
    """
    Get the most accurate timestamp for a file, preferring metadata over filesystem times.
    """
    ext = path.suffix.lower()[1:]
    func_mapping = {
        "jpg": get_timestamp_by_jpeg_metadata,
        "jpeg": get_timestamp_by_jpeg_metadata,
        "png": get_timestamp_by_png_metadata,
        "heic": get_timestamp_by_heic_metadata,
    }
    timestamp = func_mapping.get(ext, get_timestamp_by_file_system)(path)
    if timestamp is None:
        timestamp = get_timestamp_by_file_system(path)
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
    processed: int,
    size_increment: int,
) -> tuple[int, int, int]:
    """
    Recursively processes files from the source directory, copying them to the target directory
    while updating a file sync database. Tracks the number of files copied and the total size.

    Args:
        - source_dir (Path): The directory to process.
        - target_dir (Path): The destination directory for the files.
        - conn (sqlite3.Connection): The database connection for file sync tracking.
        - processed (int): The number of files processed, updated during recursion.
        - size_increment (int): The total size of processed files, updated during recursion.

    Returns:
        tuple[int, int, int]:
            - exit_code (int): 0: successful, 1: interrupted, 2: disconnected.
            - processed (int): Updated file count.
            - size_increment (int): Updated total file size in bytes.

    Notes:
        - Copies files to a subdirectory based on their timestamp.
        - If file with the same name exists, an increment suffix will be appended.
        - Skips already processed files and handles errors like permission issues or insufficient space.
    """
    try:
        dirs = [path for path in source_dir.iterdir() if path.is_dir()]
        dirs = sorted(dirs)
        files = [path for path in source_dir.iterdir() if path.is_file()]
        exit_code = 0

        # depth first
        for dir in dirs:
            exit_code, processed, size_increment = process_dir_recursively(
                dir, target_dir, conn, processed, size_increment
            )
            if exit_code != 0:
                return exit_code, processed, size_increment

        if not files:
            return exit_code, processed, size_increment
        for file_path in tqdm(
            files,
            desc=f"syncing : {source_dir.name:<18} |",
            bar_format="{desc} {bar} [{n_fmt}/{total_fmt}]",
            ncols=80,
            miniters=1,
        ):
            source = str(Path(*file_path.parts[-2:]))
            if is_unwanted_file(source):
                continue
            if is_processed_source(source, conn):
                continue
            file_name = file_path.name
            file_timestamp = get_timestamp(file_path)
            year_month = file_timestamp.strftime("%Y-%m")
            target_subdir = target_dir / year_month
            target_subdir.mkdir(parents=True, exist_ok=True)
            target_file_path = target_subdir / file_name

            try:
                with tempfile.NamedTemporaryFile(dir=target_subdir, delete=False) as temp_file:
                    shutil.copy2(file_path, temp_file.name)
                    os.replace(temp_file.name, target_file_path)
            except OSError as e:
                if e.errno == errno.EACCES:
                    print("[pyphotobackups] permission denied")
                    raise Abort
                if e.errno == errno.ENOSPC:
                    print("[pyphotobackups] no enough space in destination directory")
                    raise Abort
                raise e

            processed += 1
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
        print("[pyphotobackups] interrupted!")
        exit_code = 1
    except OSError as e:
        if e.errno == errno.EIO:
            print("[pyphotobackups] io error occurred! did you remove your iPhone connection?")
            exit_code = 2
        else:
            raise e
    return exit_code, processed, size_increment
