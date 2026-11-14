from pathlib import Path
import json
import tempfile
import unittest
import evidence_context as E


class EvidenceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name);self.root = self.base/'source';self.root.mkdir()
        self.runtime = self.base/'runtime';self.folder = self.runtime/'assessments'/'baseline'
        self.folder.mkdir(parents=True)
        self.state = {'last_assessment': {'run': 'baseline'}}
        self.task = {'id': 'repair', 'files': ['tests/fixture.test.ts']}
        self.write('assessment-base.json', {'head': 'a'*40, 'profile_hash': 'p'})
        self.write('checks.json', [{'command': ['node','test'], 'code': 1, 'reason': 'exited'}])
        (self.folder/'check-0.log').write_text('PASS unrelated\nFAIL tests/fixture.test.ts\nExpected 2, received 3\n')

    def write(self, name, value):
        (self.folder/name).write_text(json.dumps(value))

    def collect(self, **kw):
        return E.collect(self.root, self.runtime, self.task, self.state, 'a'*40, profile_hash='p', **kw)

    def test_actual_canonical_failure_is_inline_bound_and_read_only(self):
        before={p:p.read_bytes() for p in self.folder.iterdir()}
        value=self.collect()
        self.assertIn('Expected 2, received 3',json.dumps(value))
        self.assertEqual(value['canonical_or_attempt_failures'][0]['origin'],'canonical')
        self.assertEqual(before,{p:p.read_bytes() for p in self.folder.iterdir()})
        self.assertEqual(value['evidence_hash'],self.collect()['evidence_hash'])
        (self.folder/'check-0.log').write_text('FAIL changed concrete evidence')
        self.assertNotEqual(value['evidence_hash'],self.collect()['evidence_hash'])

    def test_wrong_source_or_profile_is_not_current_canonical_evidence(self):
        self.write('assessment-base.json',{'head':'b'*40,'profile_hash':'p'})
        self.assertEqual(self.collect()['canonical_or_attempt_failures'],[])
        self.write('assessment-base.json',{'head':'a'*40,'profile_hash':'other'})
        self.assertEqual(self.collect()['canonical_or_attempt_failures'],[])

    def test_source_prefers_failing_file_and_never_follows_secret_symlinks(self):
        (self.root/'tests').mkdir();(self.root/'tests/fixture.test.ts').write_text('meaningful regression')
        secret=self.base/'secret';secret.write_text('DO_NOT_READ')
        (self.root/'tests/escape.ts').symlink_to(secret)
        (self.root/'.env').write_text('DO_NOT_READ')
        task={'files':['tests/fixture.test.ts','tests/escape.ts','.env']}
        value=E.source_context(self.root,task,[],self.collect())
        self.assertEqual(list(value),['tests/fixture.test.ts'])
        self.assertNotIn('DO_NOT_READ',json.dumps(value))

    def test_credential_redaction_and_excerpts_are_bounded(self):
        (self.folder/'check-0.log').write_text('FAIL fixture\napi_key=private-value\n'+'x'*100000)
        result=self.collect(max_bytes=12000)
        text=json.dumps(result)
        self.assertNotIn('private-value',text);self.assertIn('[REDACTED]',text)
        self.assertTrue(result['canonical_or_attempt_failures'][0]['output']['truncated'])
        self.assertLess(len(text.encode()),12000)

    def test_nested_review_and_metadata_cannot_bypass_final_encoded_budget(self):
        run=self.runtime/'runs'/'reviewed';(run/'review').mkdir(parents=True)
        (run/'review/result.json').write_text(json.dumps({'summary':'x'*900000,'api_key':'PRIVATE_TEST_SENTINEL'}))
        (run/'worker-process.json').write_text(json.dumps({'reason':'password="multi word secret"','code':1}))
        self.state['feedback_history']={'repair':[{'run':'reviewed'}]}
        value=self.collect()
        raw=json.dumps(value,ensure_ascii=False).encode()
        self.assertLessEqual(len(raw),64000)
        self.assertNotIn(b'PRIVATE_TEST_SENTINEL',raw);self.assertNotIn(b'multi word secret',raw)
        self.assertIn('[REDACTED]',E.sanitize('{"api_key": "PRIVATE_TEST_SENTINEL"}'))

    def test_parent_symlink_to_excluded_source_is_rejected(self):
        (self.root/'src').mkdir();(self.root/'.env-private').mkdir()
        (self.root/'.env-private/material.py').write_text('PRIVATE_TEST_SENTINEL')
        (self.root/'src/alias').symlink_to(self.root/'.env-private',target_is_directory=True)
        value=E.source_context(self.root,{'files':['src/alias/material.py']},[],{})
        self.assertEqual(value,{})

    def test_budget_matches_actual_ascii_escaped_manager_serialization(self):
        self.write('checks.json',[{'command':['node','test'],'code':1,'reason':'exited'}]*7)
        for index in range(7):(self.folder/f'check-{index}.log').write_text('FAIL fixture\n'+'é'*10000)
        value=self.collect()
        self.assertLessEqual(len(json.dumps(value).encode()),64000)
        findings=[{'id':'long-'+str(i)+'x'*500} for i in range(16)]
        value=self.collect(max_bytes=2048,findings=findings)
        self.assertLessEqual(len(json.dumps(value).encode()),2048)
        self.assertIn('canonical_or_attempt_failures',value);self.assertIn('attempt_receipts',value)

    def test_source_budget_includes_json_and_unavailable_metadata(self):
        (self.root/'src').mkdir()
        names=[]
        for index in range(20):
            name='src/'+('long-name-'*8)+str(index)+'.ts';names.append(name)
            (self.root/name).write_text('é'*20000 if index<10 else 'x'*(1024*1024+1))
        context=E.source_context(self.root,{'files':names},[],{},max_bytes=2048)
        self.assertLessEqual(len(json.dumps(context).encode()),2048)


if __name__=='__main__':unittest.main()
