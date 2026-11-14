import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import coordinator_install as C


class ResumerInterpreterBindingTests(unittest.TestCase):
    def definition(self, executable, root):
        with mock.patch.object(C.sys, 'executable', str(executable)), \
                mock.patch.object(C, 'ROOT', root), mock.patch.object(C, 'HERE', root/'release'):
            return C.resumer_definition({'installed': 'a'*40}, root/'installer.py', 'b'*64)

    def test_aliases_of_same_binary_have_identical_exact_definition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();binary=root/'python';binary.write_bytes(b'fixture')
            first=root/'first';second=root/'second';first.symlink_to(binary);second.symlink_to(binary)
            left=self.definition(first,root);right=self.definition(second,root)
            self.assertEqual(left,right)
            self.assertEqual(plistlib.loads(left[1])['ProgramArguments'][0],str(binary))
            different=root/'different';different.write_bytes(b'other fixture')
            self.assertNotEqual(left,self.definition(different,root))

    def test_actual_parent_and_resumed_startup_modes_bind_same_binary(self):
        source=Path(C.__file__).resolve().parent
        script=('import json,sys; from pathlib import Path; '
                'sys.path.insert(0,sys.argv[1]); import coordinator_install as C; '
                'root=Path(sys.argv[2]); C.ROOT=root; C.HERE=root/"release"; '
                'label,raw=C.resumer_definition({"installed":"a"*40},root/"installer.py","b"*64); '
                'print(json.dumps({"executable":sys.executable,"definition":raw.decode()}))')
        with tempfile.TemporaryDirectory() as temporary:
            observations=[]
            for flags in [('-I','-S','-B'),('-I','-B')]:
                result=subprocess.run([sys.executable,*flags,'-c',script,str(source),str(Path(temporary).resolve())],
                                      capture_output=True,text=True,timeout=15,check=True)
                observations.append(json.loads(result.stdout))
            self.assertEqual(observations[0]['definition'],observations[1]['definition'],observations)


if __name__=='__main__':
    unittest.main()
