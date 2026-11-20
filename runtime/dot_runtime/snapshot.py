"""Copy and verify file-addressed product candidates without following links.

Build outputs are retained: excluding an entire build directory could silently
omit the firmware or dependency inputs that verification is meant to identify.
Only version-control metadata and explicitly listed Python/tool caches are
excluded. This is integrity checking, not an OS permission boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


EXCLUDED_DIRECTORIES = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})
CHUNK_SIZE = 1024 * 1024


def _filesystem_path(path: Path) -> Path:
    """Use Win32 extended paths for file I/O without changing recorded path identities.

    No registry change is needed. Normalize before adding the prefix; UNC shares
    use the UNC device form. Link/reparse and overlap checks still apply.
    """
    if os.name != "nt":
        return Path(path)
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _reject_link(path: Path) -> os.stat_result:
    info = _filesystem_path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError(f"Links and reparse points are not candidate inputs: {path}")
    return info


def _root(path: Path, *, exists: bool) -> Path:
    absolute = Path(os.path.abspath(path))
    for component in (absolute, *absolute.parents):
        if _filesystem_path(component).exists() or _filesystem_path(component).is_symlink():
            _reject_link(component)
    if exists and not _filesystem_path(absolute).is_dir():
        raise ValueError(f"Project/snapshot directory does not exist: {absolute}")
    return absolute


def _files(root: Path):
    def walk(directory):
        for item in sorted(_filesystem_path(directory).iterdir(), key=lambda p: p.name):
            entry = directory / item.name
            info = _reject_link(entry)
            if not _filesystem_path(entry).resolve(strict=True).is_relative_to(_filesystem_path(root).resolve(strict=True)):
                raise ValueError(f"Candidate input escapes its root: {entry}")
            if stat.S_ISDIR(info.st_mode):
                if entry.name not in EXCLUDED_DIRECTORIES:
                    yield from walk(entry)
            elif stat.S_ISREG(info.st_mode):
                if entry.name != ".git":  # Git worktrees use a metadata pointer file.
                    yield entry
            else:
                raise ValueError(f"Unsupported candidate input: {entry}")
    yield from walk(root)


def _stable_digest(source: Path, destination: Path | None = None) -> str:
    from .file_access import check_content
    before = _reject_link(source)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(_filesystem_path(source), flags)
    output = None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(f"Candidate input changed while opening: {source}")
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"Candidate input is not a regular file: {source}")
            if destination is not None:
                _filesystem_path(destination.parent).mkdir(parents=True, exist_ok=True)
                output = _filesystem_path(destination).open("xb")
            digest = hashlib.sha256()
            first_chunk = True
            while chunk := stream.read(CHUNK_SIZE):
                if first_chunk:
                    check_content(chunk, source)
                    first_chunk = False
                digest.update(chunk)
                if output is not None:
                    output.write(chunk)
            after = os.fstat(stream.fileno())
            current = _reject_link(source)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                                  stat.S_IMODE(s.st_mode))
            # Compare each observation method with itself: Windows synthesizes
            # executable bits for path stat; filters may expose physical/logical sizes.
            if (identity(opened) != identity(after) or identity(before) != identity(current)
                    or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError(f"Candidate input changed while reading: {source}")
            stream.seek(0)
            repeated = hashlib.sha256()
            while chunk := stream.read(CHUNK_SIZE):
                repeated.update(chunk)
            if repeated.digest() != digest.digest() or identity(after) != identity(os.fstat(stream.fileno())):
                raise ValueError(f"Candidate input changed while rereading: {source}")
    finally:
        if output is not None:
            output.close()
    if destination is not None:
        os.chmod(_filesystem_path(destination), stat.S_IMODE(before.st_mode))
    return digest.hexdigest()


def _inventory(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): _stable_digest(p) for p in _files(root)}


def _modes(root: Path) -> dict[str, int]:
    return {p.relative_to(root).as_posix(): stat.S_IMODE(_reject_link(p).st_mode)
            for p in _files(root)}


def _manifest(files: dict[str, str], modes: dict[str, int]) -> dict:
    contents = {"files": files, "modes": modes}
    canonical = json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"id": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), **contents}


def snapshot_project(project: Path, destination: Path) -> dict:
    """Freeze a consistent copy into a new/empty, disjoint directory.

    Raises on links, special files, overlap, source changes or inconsistent
    copies. A failed copy is never a candidate; any partial directory remains
    available for diagnosis and is not silently reused or deleted.
    """
    project = _root(Path(project), exists=True)
    destination = _root(Path(destination), exists=False)
    if destination.is_relative_to(project) or project.is_relative_to(destination):
        raise ValueError("Candidate destination and product directory must be disjoint")
    disk_destination = _filesystem_path(destination)
    if disk_destination.exists() and (not disk_destination.is_dir() or any(disk_destination.iterdir())):
        raise ValueError("Candidate destination must be a new or empty directory")
    before = _inventory(project)
    modes = _modes(project)
    if not before:
        raise ValueError("Cannot freeze an empty product")
    disk_destination.mkdir(parents=True, exist_ok=True)
    copied = {}
    for relative in before:
        copied[relative] = _stable_digest(project / relative, destination / relative)
    if (before != copied or before != _inventory(project) or before != _inventory(destination)
            or modes != _modes(project) or modes != _modes(destination)):
        raise ValueError("Product changed during freezing or the candidate copy differs")
    return _manifest(copied, modes)


def verify_snapshot(path: Path, manifest: dict) -> bool:
    """Return false for tampering, additional/missing inputs or unsafe paths."""
    try:
        root = _root(Path(path), exists=True)
        if not isinstance(manifest, dict) or set(manifest) != {"id", "files", "modes"}:
            return False
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            return False
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            return False
        modes = manifest["modes"]
        if not isinstance(modes, dict) or set(modes) != set(files):
            return False
        if not all(type(mode) is int and 0 <= mode <= 0o7777 for mode in modes.values()):
            return False
        return (manifest == _manifest(files, modes) and _inventory(root) == files
                and _modes(root) == modes)
    except (OSError, ValueError, TypeError):
        return False
