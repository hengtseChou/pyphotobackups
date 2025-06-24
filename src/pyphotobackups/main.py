import argparse
import os
import platform
import sys
import uuid
from datetime import datetime
from pathlib import Path

from . import __version__
from .helpers import (
    Abort,
    cleanup_lock_file,
    convert_size_to_readable,
    create_lock_file,
    get_directory_size,
    get_serial_number,
    init_db,
    is_ifuse_installed,
    is_lock_file_exists,
    mount_iPhone,
    process_dir_recursively,
    unmount_iPhone,
)

ROOT = Path("/tmp/pyphotobackups")
MOUNT_POINT = ROOT / "iPhone"


def cli():
    parser = argparse.ArgumentParser(
        description="CLI tool to sync photos from your iPhone and organize them into YYYY-MM folders."
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="show current version",
    )
    parser.add_argument(
        "dest",
        nargs="?",
        help="destination directory",
    )
    args = parser.parse_args()

    if args.version:
        print(f"[pyphotobackups] v{__version__}")
        sys.exit(0)
    system = platform.system()
    if system != "Linux":
        raise Abort(f"{system} is currently not supported")
    if not args.dest:
        raise Abort("must provide a destination directory")
    dest = Path(args.dest)
    if not dest.exists():
        raise Abort("destination does not exist")
    if not dest.is_dir():
        raise Abort("destination is not a directory")
    if not is_ifuse_installed():
        raise Abort("command `ifuse` not found. make sure it's installed on your system")
    ROOT.mkdir(exist_ok=True)
    if is_lock_file_exists(ROOT):
        raise Abort(
            "an ongoing pyphotobackups process detected. only one process is allowed at a time"
        )
    create_lock_file(ROOT)
    mount_iPhone(MOUNT_POINT)

    conn = init_db(dest)
    start = datetime.now()
    print("[pyphotobackups] starting a new backup")
    print(f"[pyphotobackups] iPhone mounted at {str(MOUNT_POINT)}. do not remove the connection!")
    print(f"dest    : {str(dest)}")
    source = MOUNT_POINT / "DCIM"
    exit_code, new_sync, file_size_increment = process_dir_recursively(source, dest, conn, 0, 0)
    end = datetime.now()
    elapsed_time = end - start
    minutes, seconds = divmod(int(elapsed_time.total_seconds()), 60)
    print("[pyphotobackups] calculating space usage...")
    dest_size = get_directory_size(dest)

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
    cleanup()

    if exit_code == 1:
        print("[pyphotobackups] backup stopped")
    else:
        print("[pyphotobackups] backup completed")
    print("[pyphotobackups] you can now safely remove your iPhone")
    print(f"new backups       : {new_sync} ({convert_size_to_readable(file_size_increment)})")
    print(f"total space usage : {convert_size_to_readable(dest_size)}")
    print(f"elapsed time      : {minutes} min {seconds} sec")


def cleanup():
    cleanup_lock_file(ROOT)
    if MOUNT_POINT.exists() and os.path.ismount(MOUNT_POINT):
        unmount_iPhone(MOUNT_POINT)
    if ROOT.exists():
        ROOT.rmdir()


def abort():
    """
    Abort the program with exit code 1.
    """
    print("[pyphotobackups] aborting")
    sys.exit(1)


def main():
    try:
        cli()
    except Abort as e:
        print(f"[pyphotobackups] {str(e)}")
        cleanup()
        abort()
    except Exception as e:
        print(f"[pyphotobackups] unexpected error: {str(e)} ")
        cleanup()
        abort()
