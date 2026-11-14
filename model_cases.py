"""Small synthetic coding jobs; authoritative assertions live in model_bench."""
CASES = [{'id': 'strict-record',
  'source': 'def validate(value):\n    return value\n',
  'task': 'Implement validate(value) in solution.py. Accept only a plain dict with exactly keys version, name, '
          'tags. version must be integer 1, never bool. name must be a nonempty string with no whitespace at '
          'either end. tags must be a list of nonempty strings with no duplicates. Raise ValueError on all '
          'invalid inputs. Return a deep independent copy, never mutate input. Add focused unit tests in '
          'test_solution.py and run them with python3 -m unittest. Edit only solution.py and test_solution.py; no '
          'backup files. Finish promptly.'},
 {'id': 'expiry-boundary',
  'source': 'class Cache:\n'
            '    def __init__(self, clock):\n'
            '        self.clock=clock\n'
            '        self.items={}\n'
            '    def put(self, key, value, ttl):\n'
            '        self.items[key]=(value,self.clock()+ttl)\n'
            '    def get(self, key, default=None):\n'
            '        return self.items.get(key,(default,0))[0]\n',
  'task': 'Fix Cache in solution.py. Injected clock returns seconds. put requires ttl to be finite int/float >=0, '
          'excluding bool, else ValueError; reject invalid ttl before mutating anything. Store values with an '
          'absolute expiry. get(key, default=None) returns default and removes the entry when clock() >= expiry; '
          'otherwise return stored value, including None. Zero TTL expires immediately. Add focused tests in '
          'test_solution.py and run python3 -m unittest. Edit only those two files, no backup files. Finish '
          'promptly.'},
 {'id': 'redact-nested',
  'source': 'def redact(value):\n    return value\n',
  'task': 'Implement redact(value) in solution.py for JSON values. Recursively return a new dict/list, replacing '
          "values for dict keys password, token, authorization or api_key (case insensitive) with '[REDACTED]'. "
          'Traverse nested lists/dicts; preserve all other scalar values. Never modify input. Redact only whole '
          'keys, so token_count is preserved. Add focused tests in test_solution.py and run python3 -m unittest. '
          'Edit only solution.py and test_solution.py, no backup files. Finish promptly.'}]
