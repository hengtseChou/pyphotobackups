import argparse
import uuid
from datetime import datetime
from pathlib import Path

from .helpers import (
    abort,
    convert_size_to_readable,
    get_directory_size,
    get_serial_number,
    init_db,
    is_ifuse_installed,
    mount_iPhone,
    process_dir_recursively,
    unmount_iPhone,
)


def main():
    parser = argparse.ArgumentParser(
        description="CLI tool to sync photos from your iPhone and organize them into YYYY-MM folders."
    )
    parser.add_argument(
        "dest",
        help="destination directory",
    )
    args = parser.parse_args()
    dest = Path(args.dest)
    if not dest.exists():
        print("[pyphotobackups] destination does not exist")
        abort()
    if not dest.is_dir():
        print("[pyphotobackups] destination is not a directory")
        abort()
    if not is_ifuse_installed():
        print("[pyphotobackups] command ifuse not found. make sure it's installed on your system")
        abort()

    conn = init_db(dest)
    start = datetime.now()
    print("[pyphotobackups] starting a new backup")
    print(f"dest    : {str(dest)}")
    mount_point = Path("/tmp/pyphotobackups/iPhone")
    mount_iPhone(mount_point)
    source = mount_point / "DCIM"
    exit_code, new_sync, file_size_increment = process_dir_recursively(source, dest, conn, 0, 0)
    end = datetime.now()
    elapsed_time = end - start
    minutes, seconds = divmod(int(elapsed_time.total_seconds()), 60)
    print("[pyphotobackups] calculating space usage...")
    dest_size = get_directory_size(dest)
    unmount_iPhone(mount_point)

    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO run (id, serial_number, dest, start, end, elapsed_time, dest_size, dest_size_increment, new_sync) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            get_serial_number(),
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
