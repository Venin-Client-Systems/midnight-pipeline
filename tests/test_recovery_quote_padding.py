"""Trailing model formatting must not replace literal source or renew a unit."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
import project_backend as B


class QuotePaddingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.work=Path(self.tmp.name)
  self.path=self.work/'case.py';self.path.write_text('class Cases:\n    def actual_case(self):\n        value = 7\n        return value\n')
  self.task={'files':['case.py'],'local_worker_files':['case.py']}
  self.quote='    def actual_case(self):\n        value = 7'
  self.proposal={'path':'case.py','symbol':'Cases.actual_case','evidence_quote':self.quote}

 def test_trailing_padding_preserves_literal_indentation_source_hash_and_original_unit(self):
  plain=B.narrowing_anchor(self.work,self.task,self.proposal);padded={**self.proposal,'evidence_quote':self.quote+'\n\n\n'};before=copy.deepcopy(padded)
  try:anchor=B.narrowing_anchor(self.work,self.task,padded)
  except ValueError as exc:self.fail('Only trailing model padding prevented a literal current source anchor: '+str(exc))
  self.assertEqual(anchor['evidence_quote'],self.quote);self.assertTrue(anchor['evidence_quote'].startswith('    def'))
  self.assertEqual(anchor['unit_id'],plain['unit_id']);self.assertEqual(anchor['source'],plain['source']);self.assertEqual(padded,before)
  self.assertEqual(anchor['quote_normalization']['raw_sha256'],hashlib.sha256(padded['evidence_quote'].encode()).hexdigest())

 def test_interior_edits_remain_rejected(self):
  changed={**self.proposal,'evidence_quote':self.quote.replace('value = 7','value = 8')+'\n\n'}
  with self.assertRaises(ValueError):B.narrowing_anchor(self.work,self.task,changed)

 def test_raw_schema_length_limit_cannot_be_bypassed_with_padding(self):
  overlong={**self.proposal,'evidence_quote':self.quote+'\n'*1201}
  with self.assertRaises(ValueError):B.narrowing_anchor(self.work,self.task,overlong)

 def test_stripping_padding_cannot_turn_a_short_quote_into_admissible_evidence(self):
  short={**self.proposal,'evidence_quote':'value = 7'+'\n'*20}
  with self.assertRaises(ValueError):B.narrowing_anchor(self.work,self.task,short)


if __name__=='__main__':unittest.main()
