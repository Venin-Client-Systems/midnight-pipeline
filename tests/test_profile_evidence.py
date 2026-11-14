import json
from pathlib import Path
import tempfile
import unittest
import datetime as dt
from unittest import mock
import profile_evidence as E
import project_backend as B
from tests.test_profile_manager import static_profile
from quality_cycle import profile_hash


class ProfileEvidenceTests(unittest.TestCase):
    def test_backend_collects_protected_coverage_for_no_available_tool_hold(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);backend=B.load_backend(root,'notes');backend.STATE=root/'runtime';backend.STATE.mkdir()
            update={'id':'profile-one','state':'needs_manager','reason':'no available tool','commit':'a'*40,'task':{'files':['src/value.py','tests/test_value.py']}}
            incomplete={'complete':False,'complete_accepted_source':{'file':{'complete':True}},'evidence_hash':'1'*64}
            complete=dict(incomplete,complete=True,evidence_hash='2'*64)
            def git(*args,**kwargs):
                if args[0]=='clone':Path(args[-1]).mkdir()
                return update['commit'] if args[:2]==('rev-parse','HEAD') else ''
            engine=mock.Mock(profile=static_profile(),profile_hash='hash')
            with mock.patch.object(backend.PM,'pending_updates',return_value=[update]),mock.patch.object(backend.PM,'provide_evidence',return_value=dict(update,state='pending')), \
                 mock.patch.object(backend.PM,'mark_needs_manager'),mock.patch.object(backend.PE,'collect',side_effect=[incomplete,complete]), \
                 mock.patch.object(backend,'git',side_effect=git),mock.patch.object(backend,'prepare_workspace'),mock.patch.object(backend,'host_checks',return_value=True) as checks, \
                 mock.patch.object(backend,'cloud',return_value={'decision':'needs_manager','summary':'Owner must decide scope','proposal':''}):
                backend.grow_quality_profile({}, {},dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=1),engine)
            checks.assert_called_once()

    def test_long_complete_test_and_exact_required_execution_are_supplied_together(self):
        with tempfile.TemporaryDirectory() as name:
            runtime=Path(name);folder=runtime/'coverage';folder.mkdir()
            profile={'schema_version':1,'id':'fixture','version':1,'owner':'coordinator','capabilities':[],'contracts':[],
                'checks':[{'id':'full','required':True,'command':['python3','-m','unittest','discover','-v']}],
                'workflows':[],'metrics':[]}
            update={'commit':'a'*40,'task':{'files':['src/value.py','tests/test_value.py']},'evidence':{'run':'accepted'}}
            source={'src/value.py':'value=1\n','tests/test_value.py':'# regression detail\n'*350+'def test_value(): pass\n'}
            (folder/'coverage-base.json').write_text(json.dumps({'head':update['commit'],'profile_hash':profile_hash(profile)}))
            (folder/'checks.json').write_text(json.dumps([{'command':profile['checks'][0]['command'],'code':0,'reason':'exited'}]))
            (folder/'check-0.log').write_text('test_value (tests.test_value.ValueTests.test_value) ... ok\nRan 1 test\nOK\n')
            result=E.collect(runtime,runtime,update,profile,lambda *args:source[args[1].split(':',1)[1]],lambda commit,path:True,verification_dir=folder)
            self.assertTrue(result['complete']);self.assertGreater(len(result['complete_accepted_source']['tests/test_value.py']['text']),5000)
            self.assertEqual(len(result['required_discovery'][0]['observed_passed_tests']),1)
            binding=json.loads((folder/'coverage-base.json').read_text());binding['head']='b'*40
            (folder/'coverage-base.json').write_text(json.dumps(binding))
            self.assertFalse(E.collect(runtime,runtime,update,profile,lambda *args:source[args[1].split(':',1)[1]],lambda commit,path:True,verification_dir=folder)['complete'])


if __name__=='__main__':unittest.main()
