#!/usr/bin/env python3
"""Build reproducible install/update assets from the reviewed manifest, outside the repo."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

from package_harness import build
from validate_repo import ROOT


def package_release(directory):
    directory = Path(directory).resolve()
    if directory.is_relative_to(ROOT):
        raise ValueError('Put release assets outside the repository')
    version = (ROOT / 'VERSION').read_text().strip()
    result = build(directory / f'reqpilot-{version}.tgz')
    raw_manifest = (ROOT / 'MANIFEST.json').read_bytes()
    manifest = json.loads(raw_manifest)
    payload = {'format': 1, 'files': {
        name: base64.b64encode((ROOT / name).read_bytes()).decode('ascii')
        for name in sorted([*manifest['files'], 'MANIFEST.json'])}}
    archive = gzip.compress(json.dumps(payload, separators=(',', ':')).encode('utf-8'), mtime=0)
    bootstrap_abi = json.loads((ROOT / 'package.json').read_text())['reqpilot']['bootstrap_abi']
    metadata = {'format': 1, 'channel': 'stable', 'version': version, 'bootstrap_abi': bootstrap_abi,
                'sha256': hashlib.sha256(archive).hexdigest(),
                'manifest_sha256': hashlib.sha256(raw_manifest).hexdigest()}
    (directory / 'reqpilot-runtime.json.gz').write_bytes(archive)
    (directory / 'reqpilot-update.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    return {**result, 'update': metadata}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    print(json.dumps(package_release(parser.parse_args().output_dir)))
