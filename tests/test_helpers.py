import builtins
import subprocess
from datetime import datetime
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import Mock, mock_open, patch

import pytest

from pyphotobackups.helpers import (
    Abort,
    cleanup_lock_file,
    convert_size_to_readable,
    create_lock_file,
    get_db_path,
    get_directory_size,
    get_file_timestamp,
    get_photo_creation_time,
    get_serial_number,
    get_video_creation_time,
    init_db,
    is_ifuse_installed,
    is_iPhone_mounted,
    is_lock_file_exists,
    is_processed_source,
    mount_iPhone,
    process_dir_recursively,
    unmount_iPhone,
)


# Lock File Management
def test_create_lock_file(tmp_path):
    lock_file = tmp_path / "pyphotobackups.lock"
    create_lock_file(tmp_path)
    assert lock_file.exists()
    lock_file.unlink()


def test_is_lock_file_exists(tmp_path):
    lock_file = tmp_path / "pyphotobackups.lock"
    lock_file.touch()
    assert is_lock_file_exists(tmp_path) is True

    lock_file.unlink()
    assert is_lock_file_exists(tmp_path) is False


def test_cleanup_lock_file(tmp_path):
    lock_file = tmp_path / "pyphotobackups.lock"
    lock_file.touch()
    cleanup_lock_file(tmp_path)
    assert lock_file.exists() is False


# Database Management
def test_get_db_path(tmp_path):
    db_path = get_db_path(tmp_path)
    assert db_path == tmp_path / ".pyphotobackups" / "db"
    assert db_path.parent.exists()


def test_init_db_sync_table_exists(tmp_path):
    conn = init_db(tmp_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sync'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_run_tables_exists(tmp_path):
    conn = init_db(tmp_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='run'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_sync_table_columns(tmp_path):
    conn = init_db(tmp_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(sync)")
    sync_columns = {row[1]: row[2] for row in cursor.fetchall()}
    assert sync_columns == {
        "source": "TEXT",
        "dest": "TEXT",
        "timestamp": "TIMESTAMP",
        "inserted_at": "TIMESTAMP",
    }
    conn.close()


def test_init_db_run_table_columns(tmp_path):
    conn = init_db(tmp_path)
    cursor = conn.cursor()
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


def test_is_processed_source_true(tmp_path):
    conn = init_db(tmp_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sync (source, dest, timestamp, inserted_at) VALUES (?, ?, ?, ?)",
        ("source1", "dest1", datetime.now(), datetime.now()),
    )
    conn.commit()

    assert is_processed_source("source1", conn) is True

    conn.close()


def test_is_processed_source_false(tmp_path):
    conn = init_db(tmp_path)

    assert is_processed_source("sourceq", conn) is False

    conn.close()


# iPhone connection
@patch("shutil.which", return_value="/usr/bin/ifuse")
def test_is_ifuse_installed(mock_which):
    assert is_ifuse_installed() is True
    mock_which.assert_called_once_with("ifuse")


@patch("shutil.which", return_value=None)
def test_is_ifuse_not_installed(mock_which):
    assert is_ifuse_installed() is False
    mock_which.assert_called_once_with("ifuse")


def test_iPhone_mounted():
    mock_data = "something /mnt/iphone ifuse rw\n"
    with patch.object(builtins, "open", mock_open(read_data=mock_data)):
        assert is_iPhone_mounted() is True


def test_iPhone_not_mounted():
    mock_data = "something /mnt/usb vfat rw\n"
    with patch.object(builtins, "open", mock_open(read_data=mock_data)):
        assert is_iPhone_mounted() is False


@patch("pyphotobackups.helpers.subprocess.run")
def test_mount_iPhone_success(mock_subprocess_run, tmp_path):
    mock_subprocess_run.return_value = Mock(returncode=0)
    mount_point = tmp_path / "mount" / "point"

    mount_iPhone(mount_point)

    assert mount_point.exists()
    mock_subprocess_run.assert_called_once_with(
        ["ifuse", str(mount_point)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@patch("pyphotobackups.helpers.subprocess.run")
def test_mount_iPhone_not_connected(mock_subprocess_run, tmp_path):
    mock_subprocess_run.return_value = Mock(returncode=1)
    mount_point = tmp_path / "mount" / "point"

    with pytest.raises(Abort):
        mount_iPhone(mount_point)

    assert not mount_point.exists()
    mock_subprocess_run.assert_called_once_with(
        ["ifuse", str(mount_point)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@patch("pyphotobackups.helpers.subprocess.run")
def test_unmount_iPhone(mock_subprocess_run, tmp_path):
    mount_point = tmp_path / "mount" / "point"
    mount_point.mkdir(parents=True, exist_ok=True)

    unmount_iPhone(mount_point)
    assert not mount_point.exists()
    mock_subprocess_run.assert_called_once_with(["umount", str(mount_point)])


@patch("pyphotobackups.helpers.subprocess.run")
def test_get_serial_number(mock_subprocess_run):
    mock_subprocess_run.return_value = Mock(stdout="123456789\n")
    serial_number = get_serial_number()
    assert serial_number == "123456789"
    mock_subprocess_run.assert_called_once_with(
        ["ideviceinfo", "-k", "SerialNumber"], capture_output=True, text=True, check=True
    )


# Directory and File Operations
def test_get_directory_size(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("a" * 1024)  # 1 KB
    size = get_directory_size(tmp_path)
    assert size == 1024


@pytest.fixture
def fake_image_path():
    return Path("/fake/photo.jpg")


@pytest.fixture
def fake_video_path():
    return Path("/fake/video.mp4")


@patch("PIL.Image.open")
def test_get_photo_creation_time_exif(mock_open, fake_image_path):
    mock_img = Mock()
    mock_img.getexif.return_value = {36867: "2023:01:15 14:22:30"}
    mock_open.return_value = mock_img

    result = get_photo_creation_time(fake_image_path)
    assert result == datetime(2023, 1, 15, 14, 22, 30)


@patch("PIL.Image.open")
def test_get_photo_creation_time_no_exif(mock_open, fake_image_path):
    mock_img = Mock()
    mock_img.getexif.return_value = {}
    mock_open.return_value = mock_img

    result = get_photo_creation_time(fake_image_path)
    assert result is None


@patch(
    "subprocess.check_output",
    return_value='{"format": {"tags": {"creation_time": "2022-06-10T12:00:00Z"}}}',
)
def test_get_video_creation_time(mock_check_output, fake_video_path):
    result = get_video_creation_time(fake_video_path)
    assert result == datetime.fromisoformat("2022-06-10T12:00:00+00:00")


@patch("subprocess.check_output", side_effect=CalledProcessError(1, "ffprobe"))
def test_get_video_creation_time_invalid(mock_check_output, fake_video_path):
    result = get_video_creation_time(fake_video_path)
    assert result is None
    assert isinstance(result, type(None))


@patch(
    "pyphotobackups.helpers.get_photo_creation_time", return_value=datetime(2023, 1, 1, 10, 0, 0)
)
def test_get_file_timestamp_photo_metadata(mock_get_photo_creation_time, fake_image_path):
    result = get_file_timestamp(fake_image_path)
    assert result == datetime(2023, 1, 1, 10, 0, 0)


@patch(
    "pyphotobackups.helpers.get_video_creation_time", return_value=datetime(2022, 6, 10, 12, 0, 0)
)
def test_get_file_timestamp_video_metadata(mock_get_video_creation_time, fake_video_path):
    result = get_file_timestamp(fake_video_path)
    assert result == datetime(2022, 6, 10, 12, 0, 0)


@patch("pyphotobackups.helpers.get_photo_creation_time", return_value=None)
@patch.object(Path, "stat")
def test_get_file_timestamp_fallback(mock_stat, mock_get_photo_creation_time, fake_image_path):
    mock_stat.return_value.st_mtime = 1700000000

    result = get_file_timestamp(fake_image_path)
    assert result == datetime.fromtimestamp(1700000000)


def test_convert_size_to_readable_zero_bytes():
    assert convert_size_to_readable(0) == "0B"


def test_convert_size_to_readable_bytes():
    assert convert_size_to_readable(512) == "512.0B"


def test_convert_size_to_readable_kilobytes():
    assert convert_size_to_readable(1024) == "1.0K"


def test_convert_size_to_readable_megabytes():
    assert convert_size_to_readable(1048576) == "1.0M"


def test_convert_size_to_readable_gigabytes():
    assert convert_size_to_readable(1073741824) == "1.0G"


def test_convert_size_to_readable_terabytes():
    assert convert_size_to_readable(1099511627776) == "1.0T"


def test_process_dir_recursively(tmp_path):
    source_dir = tmp_path / "source"
    source_sub_dir = source_dir / "sub"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    source_sub_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "file1.txt").write_text("content1")
    (source_sub_dir / "file2.txt").write_text("content2")

    conn = init_db(tmp_path)
    exit_code, counter, size_increment = process_dir_recursively(source_dir, target_dir, conn, 0, 0)

    assert exit_code == 0
    assert counter == 2
    assert size_increment == 16
    conn.close()
