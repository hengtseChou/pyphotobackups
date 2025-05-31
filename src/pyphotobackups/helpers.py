import errno
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import filetype
from blake3 import blake3
from tqdm import tqdm


def get_db_path(target_dir: Path) -> Path:
    backup_dir = target_dir / ".pyphotobackups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir / "db"


def init_db(target_dir: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(target_dir))
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sync (
            hash TEXT PRIMARY KEY,
            source TEXT NOT NULL,
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
            source TEXT NOT NULL,
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


def get_file_hash(file_path: Path) -> str:
    return blake3(file_path.read_bytes()).hexdigest()


def get_file_timestamp(file_path: Path) -> datetime:
    mtime = file_path.stat().st_mtime
    return datetime.fromtimestamp(mtime)


def is_image_or_video(path: Path) -> bool:
    if not path.is_file():
        return False
    return filetype.is_image(str(path)) or filetype.is_video(str(path))


def is_processed_file(file_hash: str, conn: sqlite3.Connection) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sync WHERE hash = ?", (file_hash,))
    count = cursor.fetchone()[0]
    cursor.close()
    return count > 0


def abort():
    print("[pyphotobackups] aborting")
    sys.exit(1)


def process_dir_recursively(
    source_dir: Path,
    target_dir: Path,
    conn: sqlite3.Connection,
    counter: int,
    size_increment: int,
) -> tuple[int, int, int]:
    try:
        dirs = [path for path in source_dir.iterdir() if path.is_dir()]
        dirs = sorted(dirs)
        files = [path for path in source_dir.iterdir() if is_image_or_video(path)]
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
        ):
            file_hash = get_file_hash(file_path)
            if is_processed_file(file_hash, conn):
                continue
            file_name = file_path.name
            file_timestamp = get_file_timestamp(file_path)
            year_month = file_timestamp.strftime("%Y-%m")
            target_subdir = target_dir / year_month
            target_subdir.mkdir(parents=True, exist_ok=True)
            target_file_path = target_subdir / file_name
            target_file_path_tmp = target_subdir / f"{file_name}.tmp"
            try:
                # prevents incomplete files by copying to a temporary file first
                shutil.copy2(file_path, target_file_path_tmp)
                os.replace(target_file_path_tmp, target_file_path)
            except OSError as e:
                if e.errno == errno.EACCES:
                    print("[pyphotobackups] permission denied")
                elif e.errno == errno.ENOSPC:
                    print("[pyphotobackups] no enough space in destination directory")
                else:
                    print(f"[pyphotobackups] unexpected error: {str(e)} ")
                abort()

            counter += 1
            size_increment += file_path.stat().st_size
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO sync (hash, source, dest, timestamp, inserted_at) VALUES (?, ?, ?, ?, ?)",
                (
                    file_hash,
                    str(file_path),
                    str(target_file_path),
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


def get_directory_size(path: Path) -> int:
    total_size = 0
    for file in path.rglob("*"):
        if file.is_file():
            total_size += file.stat().st_size
    return total_size


def convert_size_to_readable(num: int | float) -> str:
    if num == 0:
        return "0B"
    for unit in ["B", "K", "M", "G"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}T"
