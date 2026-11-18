#!/usr/bin/env python3
"""Build a local-install npm-compatible tarball from the reviewed manifest only."""
import argparse
import gzip
import io
import json
from pathlib import Path
import tarfile

from validate_repo import ROOT, validate_repository


def build(output):
    errors = validate_repository(ROOT)
    if errors:
        raise ValueError('Repository must pass validation before packaging: ' + '; '.join(errors))
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Put the install package outside the repository')
    manifest = json.loads((ROOT / 'MANIFEST.json').read_text(encoding='utf-8'))
    names = sorted([*manifest['files'], 'MANIFEST.json'])
    if any('__pycache__' in Path(name).parts or Path(name).name.startswith('.env') or name.endswith('.pyc') for name in names):
        raise ValueError('Private or generated inputs are not install-package content')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name in names:
                data = (ROOT / name).read_bytes()
                info = tarfile.TarInfo('package/' + name)
                info.size, info.mode, info.mtime = len(data), 0o644, 0
                archive.addfile(info, io.BytesIO(data))
    with tarfile.open(output, 'r:gz') as archive:
        if set(archive.getnames()) != {'package/' + name for name in names}:
            raise ValueError('Package inventory mismatch')
    return {'path': str(output), 'files': len(names), 'version': manifest['workflow_version']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
