"""Frozen stdlib-only entry point; a bad controller import cannot disable audits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    here=Path(__file__).resolve().parent;manifest=json.loads((here/'manifest.json').read_text())
    bundle=Path(manifest['bundle']);root=Path(manifest['canonical'])
    actual={str(p.relative_to(bundle)) for p in bundle.rglob('*') if p.is_file() or p.is_symlink()}
    if actual!=set(manifest['files']):raise ValueError('Unexpected files in the frozen guardian bundle')
    for name,digest in manifest['files'].items():
        path=bundle/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Guardian recovery bundle changed; owner inspection is required')
    env=dict(os.environ,MIDNIGHT_CANONICAL_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable,'-B',str(bundle/'guardian.py'),'--root',str(root)],env=env,timeout=50).returncode


if __name__=='__main__':sys.exit(main())
