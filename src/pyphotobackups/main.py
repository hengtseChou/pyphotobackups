import argparse
import uuid
from datetime import datetime
from pathlib import Path

from .helpers import (
    abort,
    convert_size_to_readable,
    get_directory_size,
    init_db,
    process_dir_recursively,
)


def main():
    parser = argparse.ArgumentParser(
        description="Sync photos and organize them into yyyy-mm folders."
    )
    parser.add_argument(
        "-s",
        "--source",
        required=True,
        help="source directory",
        dest="source",
    )
    parser.add_argument(
        "-d",
        "--dest",
        required=True,
        help="destination directory",
        dest="dest",
    )
    args = parser.parse_args()

    source = Path(args.source)
    dest = Path(args.dest)
    if not source.exists():
        print("[pyphotobackups] source directory does not exist")
        abort()
    if not dest.exists():
        print("[pyphotobackups] destination directory does not exist")
        abort()
    elif not source.is_dir():
        print(f"[pyphotobackups] {str(source)} is not a directory")
        abort()

    conn = init_db(dest)
    start = datetime.now()
    print("[pyphotobackups] starting a new backup")
    print(f"source  : {str(source)}")
    print(f"dest    : {str(dest)}")
    exit_code, new_sync, file_size_increment = process_dir_recursively(source, dest, conn, 0, 0)
    end = datetime.now()
    elapsed_time = end - start
    minutes, seconds = divmod(int(elapsed_time.total_seconds()), 60)
    print("[pyphotobackups] calculating space usage...")
    dest_size = get_directory_size(dest)

    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO run (id, source, dest, start, end, elapsed_time, dest_size, dest_size_increment, new_sync) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            str(source.absolute()),
            str(dest.absolute()),
            start,
            end,
            f"{minutes} min {seconds} sec",
            convert_size_to_readable(dest_size),
            convert_size_to_readable(file_size_increment),
            new_sync,
        ),
    )
    conn.commit()
    cursor.close()

    if exit_code == 1:
        print("[pyphotobackups] backup stopped")
    else:
        print("[pyphotobackups] backup completed")
    print(f"new backups       : {new_sync} ({convert_size_to_readable(file_size_increment)})")
    print(f"total space usage : {convert_size_to_readable(dest_size)}")
    print(f"elapsed time      : {minutes} min {seconds} sec")


if __name__ == "__main__":
    main()
