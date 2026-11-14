"""Regression: a newer pass cannot erase the evidence for an open hold."""
import json
from pathlib import Path
import tempfile
import unittest
import evidence_context as E


class ManagerEvidenceRegressionTests(unittest.TestCase):
    def test_retained_finding_failure_and_current_pass_both_reach_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';folders=[]
            for name,code in [('old',75),('latest',0)]:
                folder=runtime/'assessments'/name;folder.mkdir(parents=True);folders.append(folder)
                (folder/'assessment-base.json').write_text(json.dumps({'head':'a'*40,'profile_hash':'p'}))
                (folder/'checks.json').write_text(json.dumps([{'command':['node','check'], 'code':code,'reason':'exited'}]))
                (folder/'check-0.log').write_text('Error: concrete retained diagnostic\n' if code else 'PASS all checks\n')
            value=E.collect(root,runtime,{'id':'planning','files':[]},{'last_assessment':{'run':'latest'}},'a'*40,
                profile_hash='p',findings=[{'id':'host-check-unit','evidence':{'regression':[str(folders[0]/'check-0.log')]}}])
            self.assertIn('concrete retained diagnostic',json.dumps(value))
            self.assertEqual(value['canonical_or_attempt_failures'][0]['code'],75)
            self.assertEqual(value['canonical_passed_checks'][0]['command'],['node','check'])
            self.assertLessEqual(len(json.dumps(value).encode()),64000)
            before=value['evidence_hash'];(folders[0]/'check-0.log').write_text('Error: changed retained cause')
            self.assertNotEqual(before,E.collect(root,runtime,{'id':'planning','files':[]},{'last_assessment':{'run':'latest'}},
                'a'*40,profile_hash='p',findings=[{'id':'host-check-unit','evidence':[str(folders[0]/'check-0.log')]}])['evidence_hash'])

    def test_finding_reference_cannot_read_other_sources_profiles_or_private_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';folder=runtime/'assessments'/'old';folder.mkdir(parents=True)
            (folder/'assessment-base.json').write_text(json.dumps({'head':'b'*40,'profile_hash':'p'}))
            (folder/'checks.json').write_text(json.dumps([{'code':1,'reason':'exited'}]))
            (folder/'check-0.log').write_text('SECRET_SENTINEL')
            outside=root/'secret.log';outside.write_text('SECRET_SENTINEL')
            result=E.collect(root,runtime,{'id':'p'}, {},'a'*40,profile_hash='p',
                findings=[{'id':'f','evidence':[str(folder/'check-0.log'),str(outside),'../secret.log']}])
            self.assertNotIn('SECRET_SENTINEL',json.dumps(result))
            self.assertEqual(result['canonical_or_attempt_failures'],[])


if __name__=='__main__':unittest.main()
