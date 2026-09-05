"""Offline SQLite maintenance interlock. All local web workers hold a shared lease."""
import fcntl
from pathlib import Path


def lease(root, exclusive=False):
    path=Path(root)
    path.mkdir(parents=True,exist_ok=True)
    handle=(path/".service.lock").open("a")
    try:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle
