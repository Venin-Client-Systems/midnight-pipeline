"""Read-only activation gate for sensors during a source-changing recovery."""
import hashlib,json,re,subprocess
from pathlib import Path
import durability as D
import evidence_context as E
from deferred_release import validation_ready


def source(root):
    try:
        value=subprocess.check_output(['/usr/bin/git','-C',str(root),'rev-parse','HEAD'],stderr=subprocess.DEVNULL,timeout=2).decode().strip()
        return value if re.fullmatch('[a-f0-9]{40}',value) else None
    except (OSError,subprocess.SubprocessError):return None


def ready(runtime,head):
    runtime=Path(runtime)
    try:
        gate=D.read_json(runtime/'sensor-release-gate.json') or {}
        relative=gate.get('proof_relative_path','')
        if head!=gate.get('expected_source') or not re.fullmatch(r'commissioning/[A-Za-z0-9_-]+/result\.json',relative):return False
        path=runtime/relative
        for name in ('observer','monitor'):
            pinned=path.parent/('observer.json' if name=='observer' else 'monitor.py')
            if (not E.safe_file(pinned,runtime) or pinned.stat().st_size>2*1024*1024
                    or hashlib.sha256(pinned.read_bytes()).hexdigest()!=gate.get(name+'_sha256')):return False
        if not E.safe_file(path,runtime) or path.stat().st_size>2*1024*1024:return False
        return validation_ready({'before':head},json.loads(path.read_text()))
    except (ValueError,OSError,TypeError,KeyError):return False
