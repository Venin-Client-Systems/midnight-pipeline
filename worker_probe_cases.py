"""Additional fixed production-sized qualification contracts and oracles.

These host-owned fixtures supplement the unchanged label/range cases. They are
never product tasks or model-selectable test commands.
"""
CASE_NAMES = ('label', 'range', 'manifest_repair', 'summary_creation')
REPRESENTATIVE_CASES = CASE_NAMES[2:]
MIN_BRIEF_BYTES = 44000
MIN_PROMPT_TOKENS = 14000
REQUIRED_READS = ('contracts.md', 'fixtures.json', 'reference.py', 'test_solution.py', 'solution.py')
LEGACY_ORACLE_SHA256 = {
    'label': '914bb4226eded6b3e70c5463b6bb505cd882fd18745ee0b73a59b6cb8576c294',
    'range': '23e5e165f7ee4f673b6adbbb7c078ff9fafc862b376e9d5be1d6656725bbd00c',
}

REPAIR_SOURCE = '''import json

def parse_manifest(text):
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("invalid manifest")
    items = value.get("items")
    if not isinstance(items, list):
        raise ValueError("invalid items")
    return items
'''
REPAIR_ORACLE = '''import json
from solution import parse_manifest

valid = [{"id": "alpha"}, {"id": "beta"}]
assert parse_manifest(json.dumps({"version": 1, "items": valid})) == valid
assert parse_manifest('{"version":1,"items":[]}') == []
for invalid in (
    {"version": True, "items": []}, {"version": 1.0, "items": []},
    {"version": 2, "items": []}, {"version": 1, "items": {}},
    {"version": 1, "items": [{"id": "same"}, {"id": "same"}]},
    {"version": 1, "items": [{"id": ""}]},
    {"version": 1, "items": [{"id": 1}]},
    {"version": 1, "items": [None]}, [],
):
    try:
        parse_manifest(json.dumps(invalid))
    except ValueError:
        pass
    else:
        raise AssertionError(repr(invalid))
print("manifest repair semantic checks passed")
'''
FEATURE_ORACLE = '''from solution import summarize

assert summarize([]) == {"total": 0, "info": 0, "warning": 0, "error": 0}
records = [{"severity": "warning"}, {"severity": "error"}, {"severity": "info"}, {"severity": "error"}]
saved = [dict(value) for value in records]
assert summarize(records) == {"total": 4, "info": 1, "warning": 1, "error": 2}
assert records == saved
for invalid in (None, {}, "error", [None], [{}], [{"severity": "ERROR"}], [{"severity": 1}], [{"severity": []}]):
    try:
        summarize(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(repr(invalid))
print("summary creation semantic checks passed")
'''


def cases():
    return [
        ('manifest_repair', REPAIR_SOURCE, REPAIR_ORACLE,
         'Repair parse_manifest(text). The decoded manifest must be a dictionary; version must have exact type int and value 1 (reject bool and float). '
         'items must be a list of dictionaries, each with a nonempty string id. IDs must be unique. Raise ValueError for every invalid shape. '
         'Return the original decoded items for valid input, including an empty list. Preserve unrelated source.'),
        ('summary_creation', None, FEATURE_ORACLE,
         'Create the absent solution.py with summarize(records). Accept a list of dictionaries with severity equal to exactly info, warning or error. '
         'Return a new dictionary with total and the three severity counts, including zero counts. Do not mutate the input. '
         'Raise ValueError for a non-list, a non-dictionary item, a missing severity or any invalid severity type/value.'),
    ]


def context_files(task, oracle):
    # Distinct, deterministic records avoid depending on private product data.
    # The functional task remains small while the surrounding host brief and
    # accumulated reads exercise the production action-selection context size.
    records = '\n'.join(
        f'Host record {i:04d}: preserve original scope and protected checks; retained sample category={i % 17}, revision={1000+i}, accepted=false. No record grants another launch.'
        for i in range(300))
    return {
        'contracts.md': task + '\n\nAll original host semantic checks remain mandatory. No network, dependency, account or product access.\n',
        'fixtures.json': '[{"version":1,"items":[{"id":"alpha"}]},{"severity":"info"},{"severity":"warning"},{"severity":"error"}]\n',
        'reference.py': '# Read-only reference vocabulary.\nSEVERITIES = ("info", "warning", "error")\n' + ''.join(f'# retained reference {i:03d}: category {i % 17}, revision {1000+i}\n' for i in range(120)),
        'test_solution.py': oracle,
        'host_context': records,
    }
