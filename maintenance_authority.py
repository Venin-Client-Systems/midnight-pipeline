"""Fresh, scoped readback of the owner's recorded automation authority.

The reviewed installer seals the applicable permission values, never credentials.
Changes to unrelated automations do not stop Midnight. Changed permission text
holds account delivery until Codex reads and reconciles the new owner policy.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile
from xml.etree import ElementTree as E
import durability as D

REGISTER=Path.home()/'Developer'/'midnight-records'/'midnight-authority.json'
POLICY=REGISTER.parent/'midnight-policies/Midnight Progress Recovery Policy.md'
CELLS={'Automations':{'A5','G5','K5','L5','M5'},'Domains & Permissions':{'A10','F10','G10','H10'}}
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def fresh_bytes(path, limit=128*1024):
    """Bounded current bytes, without checkpoint fallback or following a link."""
    path=Path(path)
    if path.parent.is_symlink():raise ValueError('Unsafe authority record directory')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit:
            raise ValueError('Authority record is not a bounded regular file')
        raw=os.read(fd,limit+1);after=os.fstat(fd);current=path.stat(follow_symlinks=False)
        identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
        if len(raw)>limit or len(raw)!=before.st_size or identity(before)!=identity(after) or identity(after)!=identity(current):
            raise ValueError('Authority record changed during its read')
        return raw
    finally:os.close(fd)


def record(path=None):
    path=Path(path) if path is not None else REGISTER
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate authority field')
            result[key]=value
        return result
    def constant(_):raise ValueError('Non-finite authority value')
    raw=fresh_bytes(path);value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=constant)
    if not isinstance(value,dict) or type(value.get('schema_version')) is not int or value['schema_version']!=1 or value.get('automation')!='AUT-0004':
        raise ValueError('Unsupported VENIN authority record')
    values=value.get('permissions')
    if not isinstance(values,dict) or set(values)!=set(CELLS):raise ValueError('Authority permission sections differ')
    for sheet,addresses in CELLS.items():
        cells=values[sheet]
        if not isinstance(cells,dict) or set(cells)!=addresses or not all(isinstance(v,str) and len(v)<=16384 for v in cells.values()):
            raise ValueError('Authority permission cells differ')
    if values['Automations']['A5']!='AUT-0004' or values['Domains & Permissions']['A10']!='Product Development':
        raise ValueError('Automation authority identity changed')
    if value.get('permission_sha256')!=digest(values):raise ValueError('Authority record permission digest differs')
    return values,{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'permission_sha256':digest(values)}


def record_identity(path=None):return record(path)[1]


def permissions(path=None):
    # Legacy workbook inspection is explicit only; missing VENIN records never
    # trigger a fallback into personal storage, including from frozen bundles.
    if path is not None and Path(path).suffix=='.xlsx':return legacy_permissions(Path(path))
    return record(path)[0]


def legacy_permissions(path):
    if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('Automation authority register is unavailable')
    with zipfile.ZipFile(path) as z:
        if any(i.file_size>8*1024*1024 for i in z.infolist()):raise ValueError('Automation authority exceeds its read bound')
        strings=[''.join(e.itertext()) for e in E.fromstring(z.read('xl/sharedStrings.xml'))]
        relationships={e.get('Id'):e.get('Target') for e in E.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets={e.get('name'):relationships[e.get('{'+NS['r']+'}id')] for e in E.fromstring(z.read('xl/workbook.xml')).find('m:sheets',NS)}
        result={}
        for sheet,addresses in CELLS.items():
            target=sheets[sheet];target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            tree=E.fromstring(z.read(target));values={}
            for cell in tree.findall('m:sheetData/m:row/m:c',NS):
                if cell.get('r') not in addresses:continue
                value=cell.find('m:v',NS)
                values[cell.get('r')]=strings[int(value.text)] if cell.get('t')=='s' else ''.join(cell.find('m:is',NS).itertext()) if cell.get('t')=='inlineStr' else value.text if value is not None else ''
            if set(values)!=addresses:raise ValueError('Automation authority cells are missing')
            result[sheet]=values
        if result['Automations']['A5']!='AUT-0004' or result['Domains & Permissions']['A10']!='Product Development':raise ValueError('Automation authority identity changed')
        return result


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def seal(root,*,expected_permission_sha256=None):
    values=permissions()
    prior=D.read_json(Path(root)/'runtime/resilience/authority.json',{})
    if expected_permission_sha256 is None and prior.get('automation')=='AUT-0004':
        expected_permission_sha256=prior.get('permission_sha256')
        if not isinstance(expected_permission_sha256,str) or len(expected_permission_sha256)!=64:raise ValueError('Existing authority seal is incomplete; reviewed reconciliation is required')
    if expected_permission_sha256 is not None and digest(values)!=expected_permission_sha256:
        raise ValueError('Current permissions differ from the exact reviewed installation authority')
    allowed=values['Automations']['K5'].lower()
    progress='progress-based orchestrator recovery' in allowed
    if 'orchestrator' not in allowed or not progress and 'four' not in allowed:
        raise ValueError('Reviewed orchestrator maintenance authority has not been recorded')
    receipt={'schema_version':2 if progress else 1,'automation':'AUT-0004','permission_sha256':digest(values),'scope':'Private checked Midnight maintenance only; product permissions unchanged'}
    D.atomic_write(Path(root)/'runtime/resilience/authority.json',(json.dumps(receipt,indent=2)+'\n').encode())
    return receipt


def check(root):
    saved=D.read_json(Path(root)/'runtime/resilience/authority.json',{})
    if saved.get('automation')!='AUT-0004' or digest(permissions())!=saved.get('permission_sha256'):
        raise ValueError('Automation permission text changed; Codex must reconcile the VENIN authority record before account delivery')
    return saved
