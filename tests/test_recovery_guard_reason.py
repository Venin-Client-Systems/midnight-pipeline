import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from action_store import Actions
import phase_runtime as P


class GuardReasonTests(unittest.TestCase):
    def run_guard(self,args,duration):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Actions(root);job=store.submit('notes',{},time.time()+20)
            self.assertTrue(store.claim(job['id'],P.lease()))
            with mock.patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}):
                with P.phase(root,'notes','host',args,root,root/'out',time.time()+duration) as identity:
                    launch={'command':args,'cwd':str(root),'slot_name':P.SLOT_NAMES[P.SLOT_FD]}
                    filename=root/'launch.json';filename.write_text(json.dumps(launch))
                    proc=subprocess.Popen([sys.executable,'-I','-B',str(Path(P.__file__).with_name('phase_guard.py')),
                        '--runtime',str(root),'--phase',identity,'--launch',str(filename),'--slot',str(P.SLOT_FD)],
                        pass_fds=(P.SLOT_FD,),start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                    output=proc.communicate(timeout=12)[0]
                    receipt=json.loads((root/'phases'/identity/'guard-result.json').read_text())
                    self.assertEqual(receipt['phase'],identity);self.assertEqual(receipt['launch_sha256'],P.fingerprint(launch))
                    self.assertEqual(receipt['guard']['pid'],proc.pid)
                    execution=next(e['lease'] for e in store.executions() if e['phase']==identity)
                    self.assertTrue(P.stop_tree(execution,grace=.05));store.clear_execution(identity,execution)
                    return proc.returncode,receipt,output

    def test_actual_command_exit_75_remains_a_completed_command(self):
        code,receipt,_=self.run_guard([sys.executable,'-c','raise SystemExit(75)'],8)
        self.assertEqual(code,75);self.assertEqual(receipt['reason'],'exited')

    def test_guard_deadline_is_retained_instead_of_becoming_a_product_failure(self):
        code,receipt,_=self.run_guard([sys.executable,'-c','import time; time.sleep(20)'],1.5)
        self.assertEqual(code,75);self.assertEqual(receipt['reason'],'task_timeout')


if __name__=='__main__':unittest.main()
