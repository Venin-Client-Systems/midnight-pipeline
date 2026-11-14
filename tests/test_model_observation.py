import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import model_manager as M
import model_observation as O

class ModelObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.patch=mock.patch.object(M,'RUNTIME',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.identity='20261031T100000Z-abcdef01'
        self.state={'active':{'model':'fixture'},'comparison':{'id':self.identity,'results':[{'side':'incumbent','case':'record','passed':True,'seconds':12},{'side':'challenger','case':'record','passed':False,'seconds':20}],'incumbent':{},'challenger':{},'suite':'fixture'},'operation':{'phase':'between_probes'},'history':[]}
        M.save(self.state)
    def test_file_only_scores_and_phase_reflect_actual_completed_probes(self):
        with mock.patch.object(M.B,'api',side_effect=AssertionError('No model calls')):
            view=O.snapshot()
        self.assertEqual(view['display_phase'],'qualification_queued')
        self.assertEqual(view['scores']['challenger'],{'completed':1,'passed':0,'seconds':20})
        self.assertEqual(view['probe_results'][0]['case'],'record')
    def test_inspection_is_bounded_and_rejects_symlinks_and_invalid_ids(self):
        folder=self.root/'comparisons'/self.identity;folder.mkdir(parents=True);(folder/'plan.json').write_text('{}')
        runs=self.root/'runs'/self.identity;runs.mkdir(parents=True);probe=runs/'00-incumbent';probe.mkdir()
        (probe/'result.json').write_text('{"case":"record","passed":true,"seconds":12}')
        value=O.inspect_model('/api/model-comparison?id='+self.identity)
        self.assertTrue(value['live']);self.assertEqual(value['results'][0]['side'],'incumbent')
        outside=self.root.parent/(self.root.name+'-private');outside.write_text('"PRIVATE"');self.addCleanup(outside.unlink)
        (folder/'plan.json').unlink();(folder/'plan.json').symlink_to(outside)
        value=O.inspect_model('/api/model-comparison?id='+self.identity)
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertTrue(value['errors'])
        for query in ['id=../escape','id='+self.identity+'&id='+self.identity,'id='+self.identity+'&file=/etc/passwd']:
            with self.assertRaises(ValueError):O.inspect_model('/api/model-comparison?'+query)
    def test_changed_comparison_is_archived_and_malformed_receipts_are_visible(self):
        folder=self.root/'comparisons'/self.identity;folder.mkdir(parents=True);(folder/'plan.json').write_text('{broken')
        state=dict(self.state);state.pop('comparison');M.save(state)
        value=O.inspect_model('/api/model-comparison?id='+self.identity)
        self.assertFalse(value['live']);self.assertTrue(value['errors'])

if __name__=='__main__':unittest.main()
