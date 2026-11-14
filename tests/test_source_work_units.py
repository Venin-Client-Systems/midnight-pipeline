"""Regressions for the real retained broad-criterion admission stall."""
import json,tempfile,unittest
from pathlib import Path
import project_backend as B
import progress_recovery as P

SOURCE='''class TransportTests:
    def invalid_json(self):
        with self.assertRaisesRegex(TransportError, "Response was not valid JSON"):
            request(b"not-json")

    def invalid_utf8(self):
        with self.assertRaisesRegex(TransportError, "Response was not valid JSON"):
            request(b"\\xff")
'''

class SourceWorkUnitTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.source=self.root/'test_value.py';self.source.write_text(SOURCE)
  self.task={'id':'repair','files':['test_value.py'],'acceptance':['Exact sanitized errors and output coverage','Final integration']}
  self.scope=P.scope('notes','behavior:1',self.task,'a'*40,'profile','micro-1');self.model={'model':'fixture','digest':'a'*64}
  self.r=P.Recovery(self.root,'notes');self.r.enroll(self.scope,self.task,self.model,legacy_runs=['legacy'],legacy_tries=4)
  self.review={'decision':'accept','issues':[]};self.result=self.root/'review.json';self.result.write_text(json.dumps(self.review))
 def anchor(self,symbol='TransportTests.invalid_json',quote='request(b"not-json")',path='test_value.py'):
  return B.narrowing_anchor(self.root,self.task,{'path':path,'symbol':symbol,'evidence_quote':quote})
 def offer(self,anchor,obligation=0,text='Capture this exact exception and verify its sanitized text'):
  return self.r.offer_narrowing(self.scope,self.model,obligation=obligation,instruction=text,refs=[P.ref(self.result),P.ref(self.source)],review=self.review,anchor=anchor)
 def test_legacy_spent_umbrella_does_not_block_distinct_source_repairs(self):
  legacy=self.r.offer_narrowing(self.scope,self.model,obligation=0,instruction='Older unanchored broad instruction',refs=[P.ref(self.result)],review=self.review)
  self.r.reserve(self.scope,'legacy-noop',self.model)
  first=self.offer(self.anchor());self.assertNotEqual(first,legacy);self.r.reserve(self.scope,'json-repair',self.model)
  second=self.offer(self.anchor('TransportTests.invalid_utf8','            request(b"\\xff")'))
  self.assertNotEqual(first,second);self.assertEqual(self.r.admission(self.scope,self.model)['proof'],second)
  self.r.reserve(self.scope,'utf8-repair',self.model)
  history=self.r.narrowing_history(self.scope);self.assertEqual(len(history),3);self.assertTrue(all(p['charged'] for p in history))
  self.assertEqual(self.r.snapshot()['scopes'][0]['legacy_launches'],4)
 def test_different_prose_quote_and_obligation_cannot_renew_the_same_unit(self):
  first=self.offer(self.anchor());self.r.reserve(self.scope,'one',self.model)
  other=self.anchor(quote='with self.assertRaisesRegex(TransportError, "Response was not valid JSON"):')
  self.assertEqual(first,self.offer(other,1,'Different wording for the same method'))
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
 def test_indented_whole_function_is_an_actual_verbatim_anchor(self):
  quote=SOURCE.split('\n\n')[0].split('\n',1)[1]
  self.assertEqual(self.anchor(quote=quote)['unit_id'],self.anchor()['unit_id'])
 def test_parent_and_file_aliases_cannot_renew_a_quoted_method(self):
  anchor=self.anchor()
  for symbol in ('TransportTests','__file__'):
   other=self.anchor(symbol);self.assertEqual(other['unit_id'],anchor['unit_id']);self.assertEqual(other['unit_symbol'],'TransportTests.invalid_json')
 def test_parent_and_file_anchors_cannot_select_an_arbitrary_sibling(self):
  quote='with self.assertRaisesRegex(TransportError, "Response was not valid JSON"): '
  quote=quote.rstrip()
  for symbol in ('TransportTests','__file__'):
   with self.assertRaises(P.RecoveryHeld):self.anchor(symbol,quote)
 def legacy(self):
  from tests.fixtures.legacy_narrowing_v1 import offer_narrowing
  anchor=self.anchor();anchor={k:anchor[k] for k in ('path','symbol','evidence_quote','source')}
  proof=offer_narrowing(self.r,self.scope,self.model,obligation=0,instruction='Capture this exact exception and verify its sanitized text',refs=[P.ref(self.result),P.ref(self.source)],review=self.review,anchor=anchor)
  self.r.reserve(self.scope,'legacy-anchored',self.model)
  return proof
 def test_baseline_anchored_proof_remains_spent_after_migration(self):
  old=self.legacy()
  with self.r.store.reader() as con:before=dict(con.execute('SELECT * FROM pr_proofs WHERE id=?',(old,)).fetchone())
  self.assertEqual(self.r.narrowing_history(self.scope)[0]['anchor']['unit_id'],self.anchor()['unit_id'])
  self.assertEqual(self.offer(self.anchor(),1,'New prose and criterion index'),old)
  self.assertFalse(self.r.admission(self.scope,self.model)['allowed'])
  with self.r.store.reader() as con:self.assertEqual(before,dict(con.execute('SELECT * FROM pr_proofs WHERE id=?',(old,)).fetchone()))
 def test_baseline_anchored_proof_does_not_block_a_different_method(self):
  old=self.legacy();new=self.offer(self.anchor('TransportTests.invalid_utf8','            request(b"\\xff")'))
  self.assertNotEqual(old,new);self.assertEqual(self.r.admission(self.scope,self.model)['proof'],new)
 def test_missing_pinned_legacy_source_does_not_manufacture_a_fresh_unit(self):
  self.legacy();self.source.write_text(SOURCE+'\n# original pinned bytes changed\n')
  with self.assertRaises(P.RecoveryHeld):self.offer(self.anchor())
 def test_comments_formatting_docstrings_and_rename_do_not_renew_a_unit(self):
  before=self.anchor()['unit_id']
  self.source.write_text(SOURCE.replace('def invalid_json(self):','def renamed(self):\n        """New description"""\n        # A new comment').replace('request(b"not-json")','request( b"not-json" )'))
  self.assertEqual(self.anchor('TransportTests.renamed','request( b"not-json" )')['unit_id'],before)
 def test_a_semantic_change_is_distinct_but_still_requires_fresh_review(self):
  before=self.anchor()['unit_id'];self.source.write_text(SOURCE.replace('b"not-json"','b"other-invalid-json"'))
  after=self.anchor(quote='request(b"other-invalid-json")');self.assertNotEqual(after['unit_id'],before)
  self.review={'decision':'revise','issues':['Changed source is not independently accepted']};self.result.write_text(json.dumps(self.review))
  with self.assertRaises(P.RecoveryHeld):self.offer(after)
 def test_host_rejects_misattributed_quoted_work(self):
  with self.assertRaises(P.RecoveryHeld):self.anchor('TransportTests.invalid_utf8')
 def test_invalid_host_unit_cannot_create_proof(self):
  with self.assertRaises(P.RecoveryHeld):self.offer({'unit_id':'model-invented'})
 def test_history_read_is_read_only_even_when_store_is_absent(self):
  missing=self.root/'missing';self.assertEqual(P.Recovery(missing,'notes').narrowing_history(self.scope),[]);self.assertFalse(missing.exists())
 def test_non_python_fallback_cannot_renew_by_comment_quote_or_symbol(self):
  self.source=self.root/'test_value.ts';self.task['files']=['test_value.ts'];self.source.write_text('// old comment\nfunction failure() { check("bad response"); }\n')
  first=self.anchor('failure','check("bad response");','test_value.ts')['unit_id']
  self.source.write_text('// new comment\nfunction renamed() { check( "bad response" ); }\n')
  self.assertEqual(first,self.anchor('renamed','check( "bad response" );','test_value.ts')['unit_id'])

if __name__=='__main__':unittest.main()
