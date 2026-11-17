"""Run a pinned Doxygen with a controlled configuration for owned C sources.

This is a documentation-structure gate, not a C compiler, a complete Huawei
checker, or proof that comments describe real behaviour. Only Doxygen-visible
entities in the approved .c/.h scope are inspected. No product Doxyfile, input
filter, shell command, or external diagram renderer is enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET


LIMITATIONS = [
    "Only explicitly selected owned .c/.h files and entities visible to Doxygen are checked.",
    "Doxygen is not a full C compiler: inactive preprocessor branches, generated APIs and unsupported syntax require separate review.",
    "Documentation presence, parameter names/directions and non-void return text do not prove semantic correctness, truthful metadata or complete contracts.",
    "Function-like macro parameter contracts, evaluation count and side effects still require independent review; this XML gate checks macro documentation presence.",
    "Simple internal functions also require structural documentation in this reference gate; project-specific scope changes require plan review.",
    "This local tool is not an OS sandbox; the approved Doxygen executable and selected source tree must be trusted.",
]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_digest(files: dict[str, str]) -> str:
    return _sha(json.dumps(files, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8"))


def _safe_path(path: Path, *, exists: bool = True) -> Path:
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"Linked/reparse paths are not permitted: {item}")
    if exists and not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    return path


def source_manifest(source_root: Path, includes: list[str] | None = None) -> dict[str, str]:
    """Return selected relative POSIX filenames and SHA256, rejecting links."""
    root = _safe_path(source_root)
    if not root.is_dir():
        raise ValueError("source_root must be a directory")
    paths: set[Path] = set()
    for name in includes or ["."]:
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("include entries must be nonempty relative paths without '..'")
        selected = _safe_path(root / name)
        if not selected.is_relative_to(root):
            raise ValueError("include path escapes source_root")
        if selected.is_dir():
            for folder, dirs, files in os.walk(selected, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for child in dirs + files:
                    _safe_path(Path(folder) / child)
                for file in files:
                    item = Path(folder) / file
                    if item.suffix.lower() in (".c", ".h"):
                        paths.add(item)
        elif selected.is_file() and selected.suffix.lower() in (".c", ".h"):
            paths.add(selected)
        else:
            raise ValueError(f"include is not a C/header file or directory: {name}")
    if not paths:
        raise ValueError("No owned .c/.h source files selected")
    result = {}
    for path in sorted(paths):
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"Source is not a regular file: {path}")
        _quote(path)
        result[path.relative_to(root).as_posix()] = _sha(path.read_bytes())
    return result


def _quote(path: Path) -> str:
    text = path.as_posix()
    if any(ord(c) < 32 for c in text) or any(c in text for c in ('"', '$')):
        raise ValueError("Doxyfile paths may not contain control characters, quotes or '$'")
    return '"' + text + '"'


def build_config(source_root: Path, source_files: dict[str, str], output_dir: Path) -> str:
    """Return the exact controlled UTF-8 config; callers may independently hash it."""
    root, output = Path(source_root).absolute(), Path(output_dir).absolute()
    values = {
        "DOXYFILE_ENCODING": "UTF-8", "PROJECT_NAME": '"Owned C documentation gate"',
        "OUTPUT_DIRECTORY": _quote(output / "generated"),
        "INPUT": " ".join(_quote(root / name) for name in sorted(source_files)),
        "INPUT_ENCODING": "UTF-8", "FILE_PATTERNS": "*.c *.h", "RECURSIVE": "NO",
        "OPTIMIZE_OUTPUT_FOR_C": "YES", "EXTRACT_ALL": "NO",
        "EXTRACT_STATIC": "YES", "EXTRACT_PRIVATE": "YES", "INTERNAL_DOCS": "YES",
        "HIDE_UNDOC_MEMBERS": "NO", "HIDE_UNDOC_CLASSES": "NO",
        "FULL_PATH_NAMES": "YES", "STRIP_FROM_PATH": _quote(root),
        "WARNINGS": "YES", "WARN_IF_UNDOCUMENTED": "YES",
        "WARN_IF_DOC_ERROR": "YES", "WARN_NO_PARAMDOC": "YES",
        "WARN_IF_UNDOC_ENUM_VAL": "YES", "WARN_AS_ERROR": "NO",
        "WARN_LOGFILE": _quote(output / "warnings.log"), "QUIET": "YES",
        "GENERATE_HTML": "NO", "GENERATE_LATEX": "NO", "GENERATE_RTF": "NO",
        "GENERATE_MAN": "NO", "GENERATE_DOCBOOK": "NO", "GENERATE_XML": "YES",
        "XML_OUTPUT": "xml", "XML_PROGRAMLISTING": "NO",
        "SOURCE_BROWSER": "NO", "INLINE_SOURCES": "NO", "HAVE_DOT": "NO",
        "CALL_GRAPH": "NO", "CALLER_GRAPH": "NO",
        "ENABLE_PREPROCESSING": "YES", "MACRO_EXPANSION": "NO", "SEARCH_INCLUDES": "NO",
        "INPUT_FILTER": "", "FILTER_PATTERNS": "", "FILTER_SOURCE_FILES": "NO",
        "FILTER_SOURCE_PATTERNS": "", "FILE_VERSION_FILTER": "", "ALIASES": "",
        "EXAMPLE_PATH": "", "IMAGE_PATH": "", "DOTFILE_DIRS": "",
        "MSCFILE_DIRS": "", "DIAFILE_DIRS": "", "TAGFILES": "",
    }
    return "# Generated by check_doxygen.py; no product configuration is loaded.\n" + "".join(
        f"{key} = {value}\n" for key, value in values.items())


def _terminate_tree(process: subprocess.Popen, output_dir: Path) -> bool:
    """Confirm tree termination; inability to confirm yields UNKNOWN."""
    try:
        if os.name == "nt":
            with (output_dir / "termination.log").open("ab") as log:
                killed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        timeout=15, shell=False,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process.wait(timeout=5)
            return killed.returncode == 0
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False
    except (OSError, subprocess.SubprocessError):
        return False


def _run(argv: list[str], phase: str, output: Path, timeout: int) -> dict:
    entry = {"phase": phase, "argv": argv, "exit_code": None, "stdout": "", "stderr": "",
             "stdout_path": str(output / f"{phase}.stdout.log"),
             "stderr_path": str(output / f"{phase}.stderr.log"), "status": "ERROR"}
    process = None
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    with Path(entry["stdout_path"]).open("xb") as stdout, Path(entry["stderr_path"]).open("xb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=output, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, shell=False, **options)
            entry["process_id"] = process.pid
            try:
                process.wait(timeout=timeout)
                entry["status"] = "PASS" if process.returncode == 0 else "FAIL"
            except subprocess.TimeoutExpired:
                entry["status"] = "TIMEOUT" if _terminate_tree(process, output) else "UNKNOWN"
            entry["exit_code"] = process.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            stderr.write(str(exc).encode("utf-8", errors="replace"))
            if process is not None and process.poll() is None:
                entry["status"] = "ERROR" if _terminate_tree(process, output) else "UNKNOWN"
    for stream in ("stdout", "stderr"):
        entry[stream] = Path(entry[stream + "_path"]).read_bytes().decode("utf-8", errors="replace")
    return entry


def _text(node: ET.Element | None) -> str:
    return "" if node is None else " ".join("".join(node.itertext()).split())


def _documented(node: ET.Element) -> bool:
    return bool(_text(node.find("briefdescription")) or _text(node.find("detaileddescription")))


def _read_xml(path: Path) -> ET.Element:
    _safe_path(path)
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    if not text.strip() or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper() or "\x00" in text:
        raise ValueError(f"Empty XML or forbidden XML declarations: {path.name}")
    return ET.fromstring(text)


def _check_xml(output: Path, root: Path, files: dict[str, str]) -> tuple[dict, dict, list[str]]:
    folder = output / "generated" / "xml"
    findings, hashes = [], {}
    counts = {"source_file_count": len(files), "compound_count": 0, "member_count": 0, "parameter_count": 0}
    index = _read_xml(folder / "index.xml")
    if index.tag != "doxygenindex" or not index.findall("compound"):
        raise ValueError("Doxygen XML index is empty or invalid")
    hashes["generated/xml/index.xml"] = _sha((folder / "index.xml").read_bytes())
    source_paths = {os.path.normcase(str(root / name)): name for name in files}
    seen_files, seen_members = set(), set()

    def owned(location: ET.Element | None) -> str | None:
        if location is None or not location.get("file"):
            return None
        name = Path(location.get("file", ""))
        if not name.is_absolute():
            name = root / name
        return source_paths.get(os.path.normcase(os.path.abspath(name)))

    for item in index.findall("compound"):
        ref = item.get("refid", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", ref) or ".." in ref:
            raise ValueError("Unsafe or missing XML compound reference")
        path = folder / (ref + ".xml")
        tree = _read_xml(path)
        if tree.tag != "doxygen":
            raise ValueError(f"Invalid compound XML root: {path.name}")
        hashes[path.relative_to(output).as_posix()] = _sha(path.read_bytes())
        compound = tree.find("compounddef")
        if compound is None or compound.get("id") != ref:
            raise ValueError(f"Missing or mismatched XML compound: {ref}")
        source = owned(compound.find("location"))
        kind, name = compound.get("kind"), _text(compound.find("compoundname"))
        if kind in ("file", "struct", "union", "class"):
            if source is None:
                raise ValueError(f"Compound source is outside selected files or missing: {name}")
            counts["compound_count"] += 1
            if kind == "file":
                seen_files.add(source)
            if not _documented(compound):
                findings.append(f"{source}: undocumented {kind} {name}")
        for member in compound.findall("./sectiondef/memberdef"):
            location = owned(member.find("location"))
            if location is None:
                findings.append(f"{name}: member {_text(member.find('name'))} has missing/out-of-scope source location")
                continue
            identifier = member.get("id", "")
            if identifier and identifier in seen_members:
                continue
            seen_members.add(identifier)
            counts["member_count"] += 1
            label = f"{location}: {member.get('kind')} {_text(member.find('name'))}"
            if not _documented(member):
                findings.append(label + " has no documentation")
            for value in member.findall("enumvalue"):
                if not _documented(value):
                    findings.append(label + f" value {_text(value.find('name'))} has no documentation")
            if member.get("kind") != "function":
                continue
            params = []
            for param in member.findall("param"):
                typename = _text(param.find("type"))
                pname = _text(param.find("declname")) or _text(param.find("defname"))
                if typename == "void" and not pname:
                    continue
                if not pname:
                    findings.append(label + " has unnamed/unsupported parameter; manual contract review required")
                    continue
                params.append(pname)
            documented = []
            for plist in member.findall(".//parameterlist[@kind='param']"):
                for pitem in plist.findall("parameteritem"):
                    for pname in pitem.findall("./parameternamelist/parametername"):
                        text = _text(pname)
                        documented.append(text)
                        direction = pname.get("direction", "").replace(" ", "")
                        if direction not in ("in", "out", "inout", "in,out"):
                            findings.append(label + f" parameter {text} lacks valid direction")
                        if not _text(pitem.find("parameterdescription")):
                            findings.append(label + f" parameter {text} has no description")
            counts["parameter_count"] += len(params)
            if sorted(params) != sorted(documented) or len(documented) != len(set(documented)):
                findings.append(label + " parameter documentation does not match its named parameters exactly")
            return_type = _text(member.find("type"))
            returns = member.findall(".//simplesect[@kind='return']")
            retval_lists = member.findall(".//parameterlist[@kind='retval']")
            is_void = return_type.split() and all(
                token in ("void", "static", "inline", "extern", "__inline")
                for token in return_type.split()) and "void" in return_type.split()
            if is_void:
                if returns or retval_lists:
                    findings.append(label + " void function must not claim a return value")
            else:
                retval_items = [item for values in retval_lists for item in values.findall("parameteritem")]
                valid_retvals = bool(retval_items) and all(
                    _text(item.find("parameterdescription"))
                    and _text(item.find("parameternamelist")) for item in retval_items)
                if not any(_text(section) for section in returns) and not valid_retvals:
                    findings.append(label + " non-void return has no return description or documented retval list")
    for name in sorted(set(files) - seen_files):
        findings.append(f"{name}: no file compound in Doxygen XML")
    if counts["compound_count"] == 0:
        findings.append("No selected source compounds were inspected")
    if counts["member_count"] == 0:
        findings.append("No C members were extracted; file-only XML cannot establish API documentation coverage")
    return counts, hashes, findings


def check_doxygen(source_root: Path, output_dir: Path, expected_version: str,
                  doxygen: str = "doxygen", timeout: int = 120,
                  includes: list[str] | None = None) -> dict:
    """Create report.json in a fresh independent evidence directory and return it."""
    root, output = _safe_path(source_root), _safe_path(output_dir, exists=False)
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Source and evidence directories must be disjoint")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Evidence directory must be new or empty")
    if not re.fullmatch(r"\d+\.\d+\.\d+", expected_version):
        raise ValueError("expected_version must be an exact release such as 1.18.0")
    if type(timeout) is not int or timeout < 1 or not isinstance(doxygen, str) or not doxygen.strip():
        raise ValueError("A nonempty executable and positive integer timeout are required")
    if "/" in doxygen or "\\" in doxygen:
        doxygen = str(_safe_path(Path(doxygen), exists=False))
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "checker": "doxygen-owned-c", "status": "ERROR",
              "method": "Pinned real Doxygen, controlled configuration, native diagnostics and XML structural inspection",
              "source_root": str(root), "source_files": {}, "source_digest": None,
              "expected_version": expected_version, "actual_version": None, "version_banner": "",
              "config_path": str(output / "Doxyfile"), "config_sha256": None,
              "commands": [], "xml_files": {}, "checks": {}, "findings": [],
              "limitations": LIMITATIONS.copy()}
    try:
        files = source_manifest(root, includes)
        report["source_files"], report["source_digest"] = files, manifest_digest(files)
        config = build_config(root, files, output).encode("utf-8")
        (output / "Doxyfile").write_bytes(config)
        report["config_sha256"] = _sha(config)
        version = _run([doxygen, "--version"], "version", output, timeout)
        report["commands"].append(version)
        report["status"] = version["status"]
        if version["status"] != "PASS":
            raise ValueError("Doxygen version command did not complete successfully")
        banner = version["stdout"].strip()
        report["version_banner"] = banner
        match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:\s+\([A-Fa-f0-9]+\))?", banner)
        report["actual_version"] = match.group(1) if match else None
        report["status"] = "FAIL"
        if report["actual_version"] != expected_version or version["stderr"].strip():
            raise ValueError("Doxygen version is not the approved exact release or produced diagnostics")
        generation = _run([doxygen, str(output / "Doxyfile")], "generate", output, timeout)
        report["commands"].append(generation)
        report["status"] = generation["status"]
        if generation["status"] != "PASS":
            raise ValueError("Doxygen generation did not complete successfully")
        report["status"] = "FAIL"
        warning_path = output / "warnings.log"
        if not warning_path.is_file():
            raise ValueError("Doxygen did not produce its configured warning log")
        report["warning_log_path"] = str(warning_path)
        report["warnings"] = warning_path.read_text(encoding="utf-8", errors="replace")
        if report["warnings"].strip() or generation["stderr"].strip() or re.search(r"\bwarning\b|\berror\b", generation["stdout"], re.I):
            report["findings"].append("Doxygen reported warnings/errors; inspect raw logs")
        counts, hashes, findings = _check_xml(output, root, files)
        report["checks"], report["xml_files"] = counts, hashes
        report["findings"].extend(findings)
        if source_manifest(root, includes) != files:
            report["findings"].append("Selected source files changed during documentation check")
        if _sha((output / "Doxyfile").read_bytes()) != report["config_sha256"]:
            report["findings"].append("Controlled configuration changed during documentation check")
        report["status"] = "FAIL" if report["findings"] else "PASS"
    except (OSError, ValueError, ET.ParseError) as exc:
        if report["status"] == "PASS":
            report["status"] = "ERROR"
        report["findings"].append(str(exc))
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--doxygen", default="doxygen")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--include", action="append", dest="includes")
    args = parser.parse_args(argv)
    try:
        report = check_doxygen(**vars(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"checker": "doxygen-owned-c", "status": "ERROR", "findings": [str(exc)]}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
