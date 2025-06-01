import subprocess
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from pyphotobackups.helpers import (
    abort,
    convert_size_to_readable,
    get_db_path,
    get_directory_size,
    get_file_timestamp,
    get_serial_number,
    init_db,
    is_ifuse_installed,
    is_processed_source,
    mount_iPhone,
    process_dir_recursively,
    unmount_iPhone,
)


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


def test_get_db_path(temp_dir):
    db_path = get_db_path(temp_dir)
    assert db_path == temp_dir / ".pyphotobackups" / "db"
    assert db_path.parent.exists()


def test_init_db(temp_dir):
    conn = init_db(temp_dir)
    cursor = conn.cursor()

    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sync'")
    assert cursor.fetchone() is not None

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='run'")
    assert cursor.fetchone() is not None

    # Check columns for 'sync' table
    cursor.execute("PRAGMA table_info(sync)")
    sync_columns = {row[1]: row[2] for row in cursor.fetchall()}
    assert sync_columns == {
        "source": "TEXT",
        "dest": "TEXT",
        "timestamp": "TIMESTAMP",
        "inserted_at": "TIMESTAMP",
    }

    # Check columns for 'run' table
    cursor.execute("PRAGMA table_info(run)")
    run_columns = {row[1]: row[2] for row in cursor.fetchall()}
    assert run_columns == {
        "id": "TEXT",
        "serial_number": "TEXT",
        "dest": "TEXT",
        "start": "TIMESTAMP",
        "end": "TIMESTAMP",
        "elapsed_time": "TEXT",
        "dest_size": "TEXT",
        "dest_size_increment": "TEXT",
        "new_sync": "INTEGER",
    }

    conn.close()


@patch("pyphotobackups.helpers.subprocess.run")
def test_get_serial_number(mock_subprocess_run):
    mock_subprocess_run.return_value = MagicMock(stdout="123456789\n")
    serial_number = get_serial_number()
    assert serial_number == "123456789"
    mock_subprocess_run.assert_called_once_with(
        ["ideviceinfo", "-k", "SerialNumber"], capture_output=True, text=True, check=True
    )


def test_get_file_timestamp(temp_dir):
    test_file = temp_dir / "test.txt"
    test_file.touch()
    timestamp = get_file_timestamp(test_file)
    assert isinstance(timestamp, datetime)
    assert timestamp == datetime.fromtimestamp(test_file.stat().st_mtime)


def test_is_processed_source(temp_dir):
    conn = init_db(temp_dir)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sync (source, dest, timestamp, inserted_at) VALUES (?, ?, ?, ?)",
        ("source1", "dest1", datetime.now(), datetime.now()),
    )
    conn.commit()

    assert is_processed_source("source1", conn) is True
    assert is_processed_source("source2", conn) is False

    conn.close()


def test_abort():
    with pytest.raises(SystemExit):
        abort()


@patch("shutil.which", return_value="/usr/bin/ifuse")
def test_is_ifuse_installed(mock_which):
    assert is_ifuse_installed() is True
    mock_which.assert_called_once_with("ifuse")


@patch("shutil.which", return_value=None)
def test_is_ifuse_not_installed(mock_which):
    assert is_ifuse_installed() is False
    mock_which.assert_called_once_with("ifuse")


@patch("pyphotobackups.helpers.abort", side_effect=SystemExit)
@patch("pyphotobackups.helpers.subprocess.run")
def test_mount_iPhone_already_exists(mock_subprocess_run, mock_abort, tmp_path):
    # Simulate the mount point already existing
    mount_point = tmp_path / "pyphotobackups" / "test"
    mount_point.mkdir(parents=True, exist_ok=True)

    # Call the function
    with pytest.raises(SystemExit):
        mount_iPhone(mount_point)

    # Assertions
    mock_abort.assert_called_once()  # Ensure abort() was called
    assert mount_point.exists()  # The directory should still exist
    mock_subprocess_run.assert_not_called()  # subprocess.run should not be called


@patch("pyphotobackups.helpers.abort", side_effect=SystemExit)
@patch("pyphotobackups.helpers.subprocess.run")
def test_mount_iPhone_success(mock_subprocess_run, mock_abort, tmp_path):
    # Simulate a successful `ifuse` command
    mock_subprocess_run.return_value = MagicMock(returncode=0)
    mount_point = tmp_path / "pyphotobackups" / "test"

    # Call the function
    mount_iPhone(mount_point)

    # Assertions
    assert mount_point.exists()  # The directory should be created
    mock_subprocess_run.assert_called_once_with(
        ["ifuse", str(mount_point)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    mock_abort.assert_not_called()  # abort() should not be called


@patch("pyphotobackups.helpers.abort", side_effect=SystemExit)
@patch("pyphotobackups.helpers.subprocess.run")
def test_mount_iPhone_not_connected(mock_subprocess_run, mock_abort, tmp_path):
    # Simulate the `ifuse` command failing (return code 1)
    mock_subprocess_run.return_value = MagicMock(returncode=1)
    mount_point = tmp_path / "pyphotobackups" / "test"

    # Call the function
    with pytest.raises(SystemExit):
        mount_iPhone(mount_point)

    # Assertions
    mock_abort.assert_called_once()  # Ensure abort() was called
    assert not mount_point.exists()  # The directory should be removed
    mock_subprocess_run.assert_called_once_with(
        ["ifuse", str(mount_point)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@patch("pyphotobackups.helpers.subprocess.run")
def test_unmount_iPhone(mock_subprocess_run, tmp_path):
    mount_point = tmp_path / "pyphotobackups" / "test"
    mount_point.mkdir(parents=True, exist_ok=True)

    unmount_iPhone(mount_point)
    assert not mount_point.exists()
    mock_subprocess_run.assert_called_once_with(["umount", str(mount_point)])


def test_get_directory_size(temp_dir):
    test_file = temp_dir / "test.txt"
    test_file.write_text("a" * 1024)  # 1 KB
    size = get_directory_size(temp_dir)
    assert size == 1024


def test_convert_size_to_readable():
    assert convert_size_to_readable(0) == "0B"
    assert convert_size_to_readable(512) == "512.0B"
    assert convert_size_to_readable(1024) == "1.0K"
    assert convert_size_to_readable(1048576) == "1.0M"
    assert convert_size_to_readable(1073741824) == "1.0G"
    assert convert_size_to_readable(1099511627776) == "1.0T"


def test_process_dir_recursively(temp_dir):
    source_dir = temp_dir / "source"
    source_sub_dir = source_dir / "sub"
    target_dir = temp_dir / "target"
    source_dir.mkdir()
    source_sub_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "file1.txt").write_text("content1")
    (source_sub_dir / "file2.txt").write_text("content2")

    conn = init_db(temp_dir)
    exit_code, counter, size_increment = process_dir_recursively(source_dir, target_dir, conn, 0, 0)

    assert exit_code == 0
    assert counter == 2
    assert size_increment == 16
    conn.close()
