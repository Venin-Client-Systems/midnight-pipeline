"""First-run commands: setup, uninstall, init, add-task and try.

Everything here writes local, untracked state only: `projects.json`, a quality
profile per project, two launchd services and a `midnight` launcher. Midnight's
own checkout stays clean, which the controller requires before it starts work.
"""
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / 'projects.json'
PROFILES = ROOT / 'quality_profiles'
RECEIPT = ROOT / 'runtime' / 'setup.json'
PYTHON = '/opt/homebrew/bin/python3'
OLLAMA = 'http://127.0.0.1:11434'
LABELS = {'controller': 'space.venin.midnight', 'dashboard': 'space.venin.midnight-dashboard'}
MARKER = '# Midnight launcher'
COMMANDS = ('setup', 'uninstall', 'init', 'add-task', 'try')
WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri']
INSTRUCTION = ('Work through the backlog one task at a time. Give the local worker one observable behaviour per task, '
               'keep the file scope explicit, keep existing tests passing and add a test for each new behaviour.')
DEMO_TASK = {
    'id': 'T001', 'kind': 'feature', 'title': 'Normalise labels',
    'description': ('Add normalize_label(value) to demo/labels.py. Accept strings, strip surrounding whitespace and '
                    'return lowercase. Raise TypeError for every non-string. Add tests in tests/test_unit_labels.py.'),
    'acceptance': ["normalize_label('  HeLLo ') returns 'hello'.", 'normalize_label(3) raises TypeError.',
                   'Existing tests keep passing.'],
    'files': ['demo/labels.py', 'tests/test_unit_labels.py']}


class SetupError(ValueError):
    """A problem the user can fix; the message says how."""


def say(text=''):
    print(text)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temporary, path)


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def ollama(path, body=None, timeout=10):
    request = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read())


def memory_bytes():
    return int(run(['/usr/sbin/sysctl', '-n', 'hw.memsize']).stdout.strip() or 0)


def system_timezone():
    try:
        target = os.readlink('/etc/localtime')
    except OSError:
        return 'UTC'
    return target.split('zoneinfo/', 1)[1] if 'zoneinfo/' in target else 'UTC'


def prerequisites():
    """Each item: (name, passed, how to fix)."""
    items = [('macOS on Apple Silicon', platform.system() == 'Darwin' and platform.machine() == 'arm64',
              'Midnight uses the macOS sandbox and launchd, and needs an Apple Silicon Mac.')]
    version = run([PYTHON, '-c', 'import sys;print(sys.version_info >= (3, 12))']).stdout.strip() if Path(PYTHON).exists() else ''
    items.append(('Python 3.12+ from Homebrew', version == 'True',
                  'Install Homebrew from https://brew.sh, then run: brew install python'))
    items.append(('Git', run(['/usr/bin/git', '--version']).returncode == 0, 'Run: xcode-select --install'))
    items.append(('macOS sandbox', Path('/usr/bin/sandbox-exec').exists(), 'sandbox-exec is part of macOS; update macOS.'))
    try:
        ollama('/api/version')
        reachable = True
    except OSError:
        reachable = False
    items.append(('Ollama running', reachable, 'Install Ollama from https://ollama.com and open it, then run setup again.'))
    return items


def local_models():
    """Installed models that can make tool calls, largest first."""
    found = []
    for entry in ollama('/api/tags').get('models', []):
        try:
            capabilities = ollama('/api/show', {'model': entry['name']}).get('capabilities', [])
        except OSError:
            continue
        if 'tools' in capabilities:
            found.append({'model': entry['name'], 'bytes': entry.get('size', 0)})
    return sorted(found, key=lambda item: -item['bytes'])


def choose_model(models, memory, wanted=None):
    if wanted:
        if wanted not in [m['model'] for m in models]:
            raise SetupError(f'{wanted} is not installed or cannot make tool calls. Run: ollama pull {wanted}')
        return wanted
    limit = memory * 0.6
    fitting = [m for m in models if m['bytes'] <= limit]
    if fitting:
        return fitting[0]['model']
    if models:
        return models[-1]['model']
    raise SetupError('No local coding model is installed. Run: ollama pull qwen3-coder:30b   (about 19 GB; needs 32 GB of memory)\n'
                     'Then run setup again. Any Ollama model with tool support works; bigger models finish more tasks.')


def new_registry(timezone, start, end, days):
    return {'schema_version': 1, 'name': 'Midnight', 'timezone': timezone,
            'schedule': {'start': start, 'end': end, 'weekdays': days}, 'projects': []}


def service(kind, timezone, port=8766):
    environment = {'PATH': f'/opt/homebrew/bin:{Path.home()}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                   'PYTHONDONTWRITEBYTECODE': '1', 'TZ': timezone}
    base = {'Label': LABELS[kind], 'WorkingDirectory': str(ROOT), 'EnvironmentVariables': environment,
            'RunAtLoad': True, 'ProcessType': 'Standard'}
    if kind == 'controller':
        return {**base, 'ProgramArguments': [PYTHON, str(ROOT / 'midnight.py'), 'tick'], 'StartInterval': 60,
                'ThrottleInterval': 30, 'StandardOutPath': str(ROOT / 'runtime/launchd.out.log'),
                'StandardErrorPath': str(ROOT / 'runtime/launchd.err.log')}
    return {**base, 'ProgramArguments': [PYTHON, '-B', str(ROOT / 'midnight.py'), 'dashboard', '--port', str(port)],
            'KeepAlive': True, 'StandardOutPath': str(ROOT / 'runtime/dashboard.out.log'),
            'StandardErrorPath': str(ROOT / 'runtime/dashboard.err.log')}


def agents_directory():
    return Path.home() / 'Library' / 'LaunchAgents'


def install_services(timezone, port, launchctl=run):
    installed = []
    domain = f'gui/{os.getuid()}'
    (ROOT / 'runtime').mkdir(mode=0o700, exist_ok=True)
    agents_directory().mkdir(parents=True, exist_ok=True)
    for kind in ('controller', 'dashboard'):
        path = agents_directory() / (LABELS[kind] + '.plist')
        path.write_bytes(plistlib.dumps(service(kind, timezone, port)))
        launchctl(['/bin/launchctl', 'bootout', f'{domain}/{LABELS[kind]}'])
        result = launchctl(['/bin/launchctl', 'bootstrap', domain, str(path)])
        if result.returncode != 0:
            raise SetupError(f'launchd refused {path.name}: {(result.stderr or result.stdout).strip()}')
        installed.append(str(path))
    return installed


def remove_services(launchctl=run):
    removed = []
    for label in LABELS.values():
        launchctl(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{label}'])
        path = agents_directory() / (label + '.plist')
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def launcher_path():
    return Path.home() / '.local' / 'bin' / 'midnight'


def install_launcher():
    path = launcher_path()
    if path.exists() and MARKER not in path.read_text(errors='replace'):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\n{MARKER}\nexec {PYTHON} "{ROOT / "midnight.py"}" "$@"\n')
    path.chmod(0o755)
    return str(path)


def setup(args):
    say('Midnight setup\n')
    failed = False
    for name, passed, fix in prerequisites():
        say(f"  {'ok ' if passed else 'MISSING'}  {name}" + ('' if passed else f'\n           {fix}'))
        failed = failed or not passed
    if failed:
        raise SetupError('Fix the items marked MISSING, then run setup again.')
    model = choose_model(local_models(), memory_bytes(), args.model)
    say(f'\n  Model: {model}')
    data = read(REGISTRY)
    if data is None:
        timezone = args.timezone or system_timezone()
        data = new_registry(timezone, args.start, args.end, WEEKDAYS if not args.every_day else WEEKDAYS + ['sat', 'sun'])
        write(REGISTRY, data)
        say(f'  Schedule: {args.start}–{args.end} {timezone}, ' + ('every day' if args.every_day else 'Monday to Friday'))
    else:
        say(f"  Schedule: kept from the existing projects.json ({data['schedule']['start']}–{data['schedule']['end']} {data['timezone']})")
    import model_manager
    model_manager.adopt(model, reason='Chosen during setup')
    receipt = {'installed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'model': model, 'services': [], 'launcher': None}
    if args.no_services:
        say('  Services: skipped (--no-services). Run the controller yourself with: midnight tick')
    else:
        receipt['services'] = install_services(data['timezone'], args.port)
        say(f'  Services: controller and dashboard loaded (dashboard at http://127.0.0.1:{args.port})')
    receipt['launcher'] = install_launcher()
    if receipt['launcher']:
        say(f"  Command: {receipt['launcher']}")
        if str(launcher_path().parent) not in os.environ.get('PATH', '').split(':'):
            say('           Add it to your PATH:  echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshrc')
    else:
        say(f'  Command: {launcher_path()} already exists and is not Midnight\'s; left alone. Use: {PYTHON} {ROOT / "midnight.py"}')
    write(RECEIPT, receipt)
    say('\nMidnight is installed. Next:\n'
        '  midnight try                 run a small demo task now and watch it work\n'
        '  midnight init ~/code/myapp   connect one of your own projects\n'
        '  midnight status              what it is doing and when it runs next')
    return 0


def uninstall(args):
    removed = remove_services()
    path = launcher_path()
    if path.exists() and MARKER in path.read_text(errors='replace'):
        path.unlink()
        removed.append(str(path))
    if args.purge:
        for target in (ROOT / 'runtime', REGISTRY):
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(str(target))
            elif target.exists():
                target.unlink()
                removed.append(str(target))
        for profile in PROFILES.glob('*.json'):
            if profile.name != 'exampleapp.json':
                profile.unlink()
                removed.append(str(profile))
    elif RECEIPT.exists():
        RECEIPT.unlink()
    for item in removed:
        say('  removed ' + item)
    say('Midnight is uninstalled.' + ('' if args.purge else
        f' Your project list and run history are kept in {ROOT}; pass --purge to delete them too.')
        + f' Delete {ROOT} to remove the program itself. Your projects were not touched.')
    return 0


def git(repository, *arguments, check=True):
    result = run(['/usr/bin/git', '-C', str(repository), *arguments])
    if check and result.returncode != 0:
        raise SetupError(f"git {' '.join(arguments)} failed in {repository}: {result.stderr.strip()}")
    return result.stdout.strip()


def slug(text):
    value = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    if not value:
        raise SetupError('Could not derive a project id; pass --id with lowercase letters, digits and hyphens.')
    return value


def discover(repository):
    """Python packages (directories with __init__.py) and the tests directory."""
    packages = sorted(p.name for p in repository.iterdir()
                      if p.is_dir() and (p / '__init__.py').exists() and p.name not in ('tests', 'test'))
    if not packages:
        raise SetupError(f'{repository} has no Python package at its top level (a directory containing __init__.py). '
                         'Midnight v0.1 works on Python projects tested with unittest.')
    if not (repository / 'tests').is_dir():
        raise SetupError(f'{repository} has no tests/ directory. Midnight proves every change by running your tests; add one first.')
    return packages


def project_config(name, data, packages):
    return {'name': name, 'timezone': data['timezone'], 'window_start': data['schedule']['start'],
            'window_end': data['schedule']['end'], 'worker_timeout_seconds': 720, 'cloud_timeout_seconds': 900,
            'max_attempts_per_night': 24, 'max_cloud_calls_per_night': 0, 'max_failures_per_night': 8,
            'source_dirs': [*packages, 'tests', 'docs'], 'product_dirs': packages,
            'source_globs': [p + '/*.py' for p in packages] + [p + '/**/*.py' for p in packages],
            'worker_allowed_patterns': [p + '/*.py' for p in packages] + [p + '/**/*.py' for p in packages] + ['tests/test_*.py', 'docs/*.md'],
            'retry_anchors': ['docs'], 'run_trailer': 'Midnight-Run'}


def quality_profile(identity, name):
    return {'schema_version': 1, 'id': identity + '-quality', 'version': 1, 'owner': 'coordinator', 'project_id': identity,
            'freshness_seconds': 86400, 'assessment_intervals': {'daily': 86400, 'weekly': 604800, 'monthly': 2592000},
            'capabilities': [{'id': 'existing-behaviour', 'status': 'shipped', 'description': f'What {name} does today, as its tests describe it'}],
            'contracts': [],
            'checks': [{'id': 'full-regression', 'required': True, 'command': ['python3', '-m', 'unittest', 'discover', '-v'], 'timeout_seconds': 240}],
            'workflows': [{'id': 'regression', 'check_ids': ['full-regression']}],
            'metrics': [{'id': 'required_check_pass_fraction', 'source': 'saved required host-check receipts for the exact head',
                         'definition': 'passed required checks divided by all required profile checks', 'periods': ['daily', 'weekly', 'monthly']},
                        {'id': 'open_material_findings', 'source': 'open defect and debt findings', 'periods': ['daily', 'weekly', 'monthly']}],
            'pipeline_metrics': [{'id': 'acceptance_rate', 'source': 'accepted-commit receipts and run outcomes', 'scope': 'development pipeline'}],
            'unmeasured_product_outcomes': [],
            'profile_update_rule': 'Midnight updates this profile after an accepted change alters what the project ships. Workers cannot edit it.'}


SUPERVISOR = '''#!/usr/bin/env python3
"""Binds this project to Midnight's shared project backend."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.environ.get("MIDNIGHT_SHARED_ROOT", {root!r}))
from project_backend import load_backend

backend = load_backend(Path(__file__).resolve().parents[1], {identity!r})
if __name__ == "__main__":
    raise SystemExit(backend.entrypoint())
'''


def registry_or_fail():
    data = read(REGISTRY)
    if data is None:
        raise SetupError('Midnight is not set up yet. Run: midnight setup')
    return data


def init(args):
    data = registry_or_fail()
    repository = Path(args.path).expanduser().resolve()
    if not (repository / '.git').exists():
        raise SetupError(f'{repository} is not a Git repository. Run `git init` and make a first commit there.')
    if run(['/usr/bin/git', '-C', str(repository), 'rev-parse', 'HEAD']).returncode != 0:
        raise SetupError(f'{repository} has no commits yet. Commit your code first.')
    if git(repository, 'status', '--porcelain'):
        raise SetupError(f'{repository} has uncommitted changes. Commit or stash them first; Midnight starts every task from a clean checkout.')
    identity = args.id or slug(repository.name)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', identity):
        raise SetupError('A project id uses lowercase letters, digits and hyphens.')
    if any(p['id'] == identity or Path(p['path']) == repository for p in data['projects']):
        raise SetupError(f'{identity} is already connected. See: midnight projects')
    name = args.name or repository.name
    packages = discover(repository)
    created = []
    orchestration = repository / 'orchestration'
    orchestration.mkdir(exist_ok=True)
    files = {orchestration / 'supervisor.py': SUPERVISOR.format(root=str(ROOT), identity=identity),
             orchestration / 'config.json': json.dumps(project_config(name, data, packages), indent=2) + '\n',
             orchestration / 'backlog.json': '[]\n',
             repository / 'ROADMAP.md': f'# {name} roadmap\n\nWhat this project is for and where it is going. Midnight gives this to the local model as background.\n'}
    for path, content in files.items():
        if not path.exists():
            path.write_text(content)
            created.append(str(path.relative_to(repository)))
    if created and not args.no_commit:
        git(repository, 'add', '--', *created)
        git(repository, 'commit', '-m', 'Add Midnight orchestration files', '--', *created)
    PROFILES.mkdir(exist_ok=True)
    write(PROFILES / (identity + '.json'), quality_profile(identity, name))
    import worker_quality
    project = {'id': identity, 'name': name, 'on_schedule': True, 'path': str(repository),
               'state_dir': str(ROOT / 'runtime' / 'projects' / identity), 'controller': 'orchestration/supervisor.py',
               'backlog': 'orchestration/backlog.json', 'config': 'orchestration/config.json', 'brief': 'ROADMAP.md',
               'why': args.why or f'{name}: kept improving by unattended overnight work.', 'instruction': INSTRUCTION,
               'local_model': (read(RECEIPT) or {}).get('model', 'local model'), 'backend_protocol': worker_quality.BACKEND_PROTOCOL,
               'quality_profile': f'quality_profiles/{identity}.json', 'github': {'enabled': False}}
    data['projects'].append(project)
    write(REGISTRY, data)
    say(f'Connected {name} as "{identity}".')
    for item in created:
        say('  added ' + item)
    if created:
        say('  ' + ('left uncommitted (--no-commit); commit them before Midnight runs' if args.no_commit else 'committed to your repository'))
    say(f'  packages Midnight may edit: {", ".join(packages)}; tests in tests/test_*.py\n'
        f'\nNext: give it something to do.\n'
        f'  midnight add-task {identity} --title "..." --description "..." --accept "..." --file {packages[0]}/module.py --file tests/test_unit_module.py')
    return 0


def next_task_id(tasks):
    numbers = [int(m.group(1)) for t in tasks if (m := re.fullmatch(r'T(\d+)', str(t.get('id', ''))))]
    return 'T%03d' % (max(numbers, default=0) + 1)


def build_task(tasks, config, *, title, description, acceptance, files, identity=None):
    task = {'id': identity or next_task_id(tasks), 'kind': 'feature', 'title': title.strip(), 'description': description.strip(),
            'acceptance': [a.strip() for a in acceptance if a.strip()], 'files': list(files)}
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', task['id']) or any(t.get('id') == task['id'] for t in tasks):
        raise SetupError(f"Task id {task['id']} is invalid or already used.")
    if not task['title'] or not task['description']:
        raise SetupError('A task needs a title and a description.')
    if not task['acceptance']:
        raise SetupError('A task needs at least one --accept line: something a test can prove.')
    if not 1 <= len(task['files']) <= 4:
        raise SetupError('A task names one to four files with --file. Split bigger work into several tasks.')
    patterns = config.get('worker_allowed_patterns', [])
    for name in task['files']:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not any(fnmatch.fnmatch(name, p) for p in patterns):
            raise SetupError(f'{name} is outside what the worker may edit ({", ".join(patterns)}). Change the file or orchestration/config.json.')
    if not any(Path(name).name.startswith('test_unit') for name in task['files']):
        raise SetupError('Include a test file named tests/test_unit_<something>.py with --file. The worker runs it while it works, and review expects a test for each change.')
    return task


def add_task(args):
    data = registry_or_fail()
    project = next((p for p in data['projects'] if p['id'] == args.project), None)
    if project is None:
        raise SetupError(f'Unknown project {args.project}. See: midnight projects')
    repository = Path(project['path'])
    backlog = repository / project['backlog']
    if git(repository, 'status', '--porcelain'):
        raise SetupError(f'{repository} has uncommitted changes. Commit or stash them first.')
    tasks = read(backlog, [])
    task = build_task(tasks, read(repository / project['config'], {}), title=args.title, description=args.description,
                      acceptance=args.accept, files=args.file, identity=args.id)
    tasks.append(task)
    write(backlog, tasks)
    git(repository, 'add', '--', project['backlog'])
    git(repository, 'commit', '-m', f"Add Midnight task {task['id']}: {task['title']}", '--', project['backlog'])
    say(f"Added {task['id']} to {project['name']} and committed the backlog. It runs in the next schedule window, or now with:\n"
        '  midnight override --for 60m --reason "run my task"')
    return 0


def create_demo(repository):
    (repository / 'demo').mkdir(parents=True)
    (repository / 'tests').mkdir()
    (repository / 'demo' / '__init__.py').write_text('"""Demo package for trying Midnight."""\n')
    (repository / 'demo' / 'text.py').write_text('"""Small text helpers."""\n\n\ndef shout(value):\n    """Return the text in capitals."""\n    return value.upper()\n')
    (repository / 'tests' / '__init__.py').write_text('')
    (repository / 'tests' / 'test_unit_text.py').write_text(
        'import unittest\n\nfrom demo.text import shout\n\n\nclass ShoutTests(unittest.TestCase):\n'
        '    def test_shout_returns_capitals(self):\n        self.assertEqual(shout("hi"), "HI")\n\n\n'
        'if __name__ == "__main__":\n    unittest.main()\n')
    (repository / 'README.md').write_text('# Midnight demo\n\nA tiny project for `midnight try`. Safe to delete.\n')
    (repository / '.gitignore').write_text('__pycache__/\n*.pyc\n')
    git(repository, 'init', '-q', '-b', 'main')
    git(repository, 'add', '-A')
    git(repository, '-c', 'user.name=Midnight demo', '-c', 'user.email=demo@midnight.invalid', 'commit', '-q', '-m', 'Demo project')
    for key, value in (('user.name', 'Midnight demo'), ('user.email', 'demo@midnight.invalid')):
        git(repository, 'config', key, value)


def try_demo(args):
    data = registry_or_fail()
    repository = Path(args.path).expanduser().resolve() if args.path else Path.home() / 'Developer' / 'midnight-demo'
    if not any(p['id'] == 'midnight-demo' for p in data['projects']):
        if repository.exists():
            raise SetupError(f'{repository} already exists. Remove it or pass a different folder: midnight try --path <folder>')
        create_demo(repository)
        say(f'Created a demo project at {repository}\n')
        init(type('Arguments', (), {'path': str(repository), 'id': 'midnight-demo', 'name': 'Midnight demo',
                                    'why': 'A tiny demo project that shows one full Midnight loop.', 'no_commit': False})())
        add_task(type('Arguments', (), {'project': 'midnight-demo', 'title': DEMO_TASK['title'], 'description': DEMO_TASK['description'],
                                        'accept': DEMO_TASK['acceptance'], 'file': DEMO_TASK['files'], 'id': None})())
    result = run([PYTHON, str(ROOT / 'midnight.py'), 'override', '--for', '90m', '--reason', 'midnight try'])
    if result.returncode != 0:
        raise SetupError('Could not open a run window: ' + (result.stderr or result.stdout).strip())
    run(['/bin/launchctl', 'kickstart', f"gui/{os.getuid()}/{LABELS['controller']}"])
    say('\nMidnight is running the demo task now (for up to 90 minutes).\n'
        '  What happens: a first review of the project, then the local model writes the change, the host reruns the tests, a\n'
        '  second pass reviews it, and an accepted change is committed. On a 32 GB Mac expect 15 to 40 minutes.\n\n'
        '  Watch:    midnight status          or open the dashboard\n'
        f'  Result:   git -C {repository} log --oneline\n'
        '  Evidence: midnight report')
    return 0


def add_parsers(sub):
    p = sub.add_parser('setup', help='Install Midnight on this Mac: model, schedule, services and the midnight command')
    p.add_argument('--model', help='Ollama model to use; default is the largest installed model that fits in memory')
    p.add_argument('--timezone', help='IANA timezone; default is this Mac\'s')
    p.add_argument('--start', default='00:00', help='Nightly window start, HH:MM (default 00:00)')
    p.add_argument('--end', default='09:00', help='Nightly window end, HH:MM (default 09:00)')
    p.add_argument('--every-day', action='store_true', help='Run at weekends too (default Monday to Friday)')
    p.add_argument('--port', type=int, default=8766, help='Dashboard port (default 8766)')
    p.add_argument('--no-services', action='store_true', help='Do not install the launchd services')
    p.add_argument('--yes', action='store_true', help='Accepted for scripts; setup never asks questions')
    p = sub.add_parser('uninstall', help='Remove the services and the midnight command')
    p.add_argument('--purge', action='store_true', help='Also delete the project list, quality profiles and all run history')
    p = sub.add_parser('init', help='Connect a Python project so Midnight can work on it')
    p.add_argument('path', help='Path to the project\'s Git repository')
    p.add_argument('--id', help='Short id; default is the folder name')
    p.add_argument('--name', help='Display name; default is the folder name')
    p.add_argument('--why', help='One sentence on what the project is for')
    p.add_argument('--no-commit', action='store_true', help='Write the orchestration files but do not commit them')
    p = sub.add_parser('add-task', help='Add a task to a connected project\'s backlog')
    p.add_argument('project')
    p.add_argument('--title', required=True)
    p.add_argument('--description', required=True, help='What to build: one observable behaviour')
    p.add_argument('--accept', action='append', default=[], required=True, help='An acceptance criterion a test can prove; repeat for more')
    p.add_argument('--file', action='append', default=[], required=True, help='A file in scope (one to four); include a tests/test_unit_*.py file')
    p.add_argument('--id', help='Task id; default is the next T-number')
    p = sub.add_parser('try', help='Create a demo project and run one task now')
    p.add_argument('--path', help='Where to create the demo project (default ~/Developer/midnight-demo)')


def main(args):
    handler = {'setup': setup, 'uninstall': uninstall, 'init': init, 'add-task': add_task, 'try': try_demo}[args.command]
    try:
        return handler(args)
    except SetupError as problem:
        print('Midnight: ' + str(problem), file=sys.stderr)
        return 2
