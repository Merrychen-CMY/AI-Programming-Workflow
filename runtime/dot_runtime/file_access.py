"""Logical file contents in the current process; no decryption or security-policy changes."""
import errno
import os
from pathlib import Path
import time


class FileAccessError(OSError):
    def __init__(self, code, path):
        self.code = code
        super().__init__(f'{code}: {path}')


def check_content(data, path):
    # A recognized envelope is evidence of an unreadable view, never valid source/JSON.
    # Other vendors are detected by expected hashes, decoding or consumer checks, not guessed.
    if data.startswith(b'%ONEDLP_HEADER%'):
        raise FileAccessError('FILE_ENCRYPTED_VIEW', path)
    return data


def read_content(path):
    return check_content(Path(path).read_bytes(), path)


def replace_record(temporary, path):
    """Retry only the same local commit; never rerun the operation that produced it.

    Keep the staged file on failure. Existing task/control locks own serialization.
    """
    for delay in (0.05, 0.1, 0.2, 0.4, 0.8, None):
        try:
            os.replace(temporary, path)
            return
        except OSError as error:
            transient = (getattr(error, 'winerror', None) in (5, 32, 33)
                         or error.errno in (errno.EACCES, errno.EPERM, errno.EBUSY))
            if not transient or delay is None:
                if transient:
                    raise FileAccessError('FILE_REPLACE_BLOCKED', path) from error
                raise
            time.sleep(delay)


def verify_written(path, expected):
    if read_content(path) != expected:
        raise FileAccessError('FILE_WRITE_VERIFY_FAILED', path)
