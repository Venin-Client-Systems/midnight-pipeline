"""Fixed host confinement for model-authored Midnight regression tests."""
import json
from pathlib import Path
import durability as D


def confined(work,directory,command,log,*,network_ports=(),receipt=None):
    work=Path(work).resolve();directory=Path(directory).resolve();log=Path(log).resolve()
    temporary=directory/'test-tmp';temporary.mkdir(exist_ok=True,mode=0o700)
    profile=directory/'validation.sb';quote=json.dumps
    lines=['(version 1)','(allow default)','(deny file-write*)','(deny network*)',
        '(deny signal)','(allow signal (target self) (target children) (target same-sandbox))',
        '(deny process-info*)','(allow process-info* (target same-sandbox))',
        f'(deny file-read* (subpath {quote(str(Path.home()))}) (subpath "/Users") (subpath "/Volumes") (subpath "/Library/Keychains"))',
        f'(allow file-read* (subpath {quote(str(work))}) (subpath {quote(str(temporary))}) (literal {quote(str(profile))}) (literal {quote(str(log))}))',
        f'(allow file-write* (subpath {quote(str(temporary))}) (literal {quote(str(log))}) (literal "/dev/null"))',
        '(deny mach-lookup (global-name "com.apple.securityd") (global-name "com.apple.security.agent") (global-name "com.apple.akd"))']
    if receipt:lines.append(f'(allow file-read* file-write* (literal {quote(str(Path(receipt).resolve()))}))')
    for port in network_ports:
        if type(port) is not int or not 1024<port<65536:raise ValueError('Invalid isolated test port')
        lines += [f'(allow network-bind (local ip "localhost:{port}"))',
            f'(allow network-inbound (local ip "localhost:{port}"))',
            f'(allow network-outbound (remote ip "localhost:{port}"))']
    for ancestor in sorted({work,directory,temporary}|set(work.parents)|set(directory.parents)):
        lines.append(f'(allow file-read-metadata (literal {quote(str(ancestor))}))')
    D.atomic_write(profile,('\n'.join(lines)+'\n').encode())
    env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin','LANG':'en_AU.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
        'HOME':str(temporary),'TMPDIR':str(temporary),'PYTHONPATH':str(work),'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_SYSTEM':'/dev/null',
        'MIDNIGHT_CONFINED_TESTS':'1'}
    return ['/usr/bin/sandbox-exec','-f',str(profile),*map(str,command)],env
