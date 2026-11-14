"""Fixed complete-suite split for macOS's non-nestable seatbelt sandbox.

Candidate/new tests run confined. Unchanged native sandbox qualification tests
run from the frozen trusted bundle, whose inputs cannot be patched by workers.
Together both parts cover every suite; neither part can be omitted at delivery.
"""
import argparse
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parent
NATIVE={'test_models','test_maintenance_sandbox'}


def expected(root,part,modules=()):
    result=[]
    for path in sorted((Path(root)/'tests').glob('test_*.py')):
        if part=='regression':
            if path.stem not in modules:continue
        elif (path.stem in NATIVE)!=(part=='native'):continue
        tree=ast.parse(path.read_text())
        for cls in tree.body:
            if isinstance(cls,ast.ClassDef):
                result += [path.stem+'.'+cls.name+'.'+node.name for node in cls.body
                           if isinstance(node,ast.FunctionDef) and node.name.startswith('test_')]
    return sorted(result)


def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item


def main():
    parser=argparse.ArgumentParser();parser.add_argument('part',choices=['candidate','native','regression']);parser.add_argument('--receipt');parser.add_argument('--token');parser.add_argument('--modules',nargs='+');args=parser.parse_args()
    sys.path.insert(0,str(ROOT))
    if args.part in {'native','regression'}:
        sys.path.insert(0,str(ROOT/'tests'))
        modules=NATIVE if args.part=='native' else args.modules or []
        if args.part=='regression' and any(not name.startswith('test_recovery_') or not name.replace('_','').isalnum() for name in modules):raise ValueError('Invalid regression module')
        selected=[c for name in sorted(modules) for c in cases(unittest.defaultTestLoader.loadTestsFromName(name))]
    else:
        suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        selected=[c for c in cases(suite) if c.__class__.__module__.split('.')[-1] not in NATIVE]
    if not selected:raise ValueError('No verification tests selected')
    started=[]
    class Result(unittest.TextTestResult):
        def startTest(self,test):started.append(test.id());super().startTest(test)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.TestSuite(selected))
    if args.receipt:
        value={'schema_version':1,'part':args.part,'token':args.token,'selected':sorted(c.id() for c in selected),
            'executed':sorted(started),'tests_run':result.testsRun,'passed':result.wasSuccessful(),
            'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)}
        with open(args.receipt,'w') as output:json.dump(value,output);output.flush();__import__('os').fsync(output.fileno())
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':sys.exit(main())
