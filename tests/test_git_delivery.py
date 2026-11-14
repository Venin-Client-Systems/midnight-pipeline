import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import git_delivery as G

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.calls=[];self.api=lambda ep,method='GET',payload=None:self.answer(ep,method,payload)
        self.config={'repository':'example-org/fixture','identity':G.IDENTITY,'base':'main'}
        self.delivery=G.GitDelivery(self.root,'fixture',self.config,api=self.api)
        self.protection={'required_status_checks':{'strict':True,'contexts':list(G.CONTEXTS)},'enforce_admins':{'enabled':True},'required_pull_request_reviews':{'required_approving_review_count':0,'require_code_owner_reviews':False}}
    def tearDown(self):self.temp.cleanup()
    def answer(self,endpoint,method,payload):
        self.calls.append((endpoint,method,payload))
        if endpoint=='user':return {'login':G.IDENTITY}
        if endpoint==self.delivery.endpoint:return {'full_name':self.config['repository'],'private':True,'permissions':{'push':True}}
        if endpoint.endswith('/protection'):return self.protection
        raise RuntimeError('Unexpected API endpoint '+endpoint)
    def test_wrong_identity_private_scope_and_repo_are_blocked(self):
        for response in [{'login':'claude-venin'},{'login':'someone-else'}]:
            with patch.object(self.delivery,'api',return_value=response):
                with self.assertRaises(ValueError):self.delivery.identity()
        for repo in ['someone/repo','example-org/../../repo','https://github.com/x']:
            with self.assertRaises(ValueError):G.GitDelivery(self.root,'fixture',dict(self.config,repository=repo))
    def test_zero_human_reviews_keep_required_checks_and_base(self):
        self.delivery.protection()
        for key,value in [('required_status_checks',{'strict':False,'contexts':list(G.CONTEXTS)}),('required_status_checks',{'strict':True,'contexts':[]}),('enforce_admins',{'enabled':False}),('required_pull_request_reviews',None),('required_pull_request_reviews',{'required_approving_review_count':1}),('allow_force_pushes',{'enabled':True}),('allow_deletions',{'enabled':True})]:
            old=self.protection.copy();self.protection[key]=value
            with self.assertRaises(ValueError):self.delivery.protection()
            self.protection=old
    def test_failed_host_checks_never_push_or_create_pr(self):
        (self.root/'candidate.patch').write_text('patch')
        value=self.delivery.deliver(self.root,{'id':'T1','title':'test'},'run1','a'*40,{'decision':'accept','issues':[]},[{'code':1,'reason':'exited'}],canonical=self.root,directory=self.root,patch_sha256=G.hashlib.sha256(b'patch').hexdigest())
        self.assertEqual(value['status'],'blocked');self.assertTrue(all(x[1]=='GET' for x in self.calls))
    def test_status_is_local_only(self):
        self.assertEqual(self.delivery.snapshot()['status'],'unobserved');self.assertEqual(self.calls,[])
    def test_plan_failure_preserves_candidate_and_pending_journal(self):
        with patch.object(self.delivery,'protection',side_effect=RuntimeError('GitHub API unavailable HTTP 403')):
            value=self.delivery.deliver(self.root,{},'run1','a'*40,{},[],canonical=self.root,directory=self.root)
        self.assertEqual(value['status'],'pending');self.assertIn('403',value['reason']);self.assertTrue((self.root/'github-delivery.json').exists())

if __name__=='__main__':unittest.main()
