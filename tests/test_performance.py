import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from performance import collect


class PerformanceTests(unittest.TestCase):
    def test_intermediate_checkpoint_is_neither_product_acceptance_nor_quality_failure(self):
        from history_store import HistoryStore
        import history_maintenance as H
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);run='20261031T060000Z-T1-stage';folder=root/'runs'/run;folder.mkdir(parents=True)
            (folder/'outcome.json').write_text(json.dumps({'outcome':'checkpoint','accepted':False,'summary':'Focused stage only'}))
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc);project=[{'id':'fixture','state_dir':name}]
            for indexed in (False,True):
                if indexed:
                    row=H.observe_run(root,run,{});HistoryStore(root).record_run('fixture',run,row,created_at=row['started_at'])
                result=collect(project,since,since+dt.timedelta(days=1))['projects'][0]
                self.assertEqual((result['accepted'],result['failed'],result['checkpoints']),(0,0,1));self.assertIsNone(result['acceptance_rate'])
    def test_first_verification_is_not_counted_as_an_avoided_review(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'runs/20261031T060000Z-T1-first';p.mkdir(parents=True)
            (p/'retry-progress.json').write_text(json.dumps({'unchanged_candidate':True,'first_verification':True,'blocked':False}))
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
            report=collect([{'id':'fixture','state_dir':name}],since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual(report['unchanged_reviews_avoided'],0)
    def test_interrupted_attempt_is_not_a_quality_failure_and_pending_review_survives(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);p=root/'runs/20261031T060000Z-T1-recovered';p.mkdir(parents=True)
            (p/'outcome.json').write_text(json.dumps({'outcome':'interrupted','summary':'Power interrupted work'}))
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
            project=[{'id':'fixture','state_dir':name}]
            report=collect(project,since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual((report['interrupted'],report['failed']),(1,0))
            (root/'state.json').write_text(json.dumps({'pending':{'run':p.name}}))
            report=collect(project,since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual((report['awaiting_review'],report['failed']),(1,0))

    def test_handoff_is_unfinished_until_independent_acceptance(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'runs/20261031T060000Z-T1-submit';p.mkdir(parents=True)
            (p/'worker-process.json').write_text(json.dumps({'code':-15,'reason':'ready_for_review','seconds':5}))
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
            report=collect([{'id':'fixture','state_dir':name}],since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual((report['accepted'],report['failed'],report['in_progress']),(0,0,1))
            self.assertEqual(report['details'][0]['worker_reason'],'ready_for_review')

    def test_counts_receipts_and_keeps_unfinished_work_separate(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            accepted=root/'runs/20261031T020000Z-T1-one'
            failed=root/'runs/20261031T030000Z-T2-two'
            pending=root/'runs/20261031T040000Z-T3-three'
            for p in (accepted,failed,pending):
                p.mkdir(parents=True)
                (p/'task.json').write_text(json.dumps({'title':p.name}))
                (p/'worker-process.json').write_text(json.dumps({'code':0,'seconds':60}))
                (p/'checks.json').write_text('[{"code":0}]')
            (accepted/'accepted.json').write_text('{"commit":"abc"}')
            (accepted/'review').mkdir()
            (accepted/'review/process.json').write_text('{"seconds":10}')
            (failed/'worker-events.jsonl').write_text('non-JSON adapter output\n{"usage":{"output_tokens":123}}\n')
            (root/'events.jsonl').write_text(json.dumps({'event':'attempt_finished','run':failed.name,'accepted':False,'summary':'Failed check'})+'\n')
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
            report=collect([{'id':'fixture','state_dir':str(root)}],since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual((report['accepted'],report['failed'],report['awaiting_review']),(1,1,1))
            self.assertEqual(report['acceptance_rate'],0.5)
            self.assertEqual(report['worker_seconds_total'],180)
            self.assertEqual(report['review_seconds_total'],10)

    def test_window_excludes_earlier_commissioning(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'runs/20261031T020000Z-T1-old';p.mkdir(parents=True)
            instant=dt.datetime(2026,10,31,5,tzinfo=dt.timezone.utc)
            report=collect([{'id':'fixture','state_dir':name}],instant,instant+dt.timedelta(hours=1))['projects'][0]
            self.assertEqual(report['runs'],0)
            self.assertIsNone(report['acceptance_rate'])

    def test_commit_recovery_receipt_remains_an_acceptance(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            p=root/'runs/20261031T060000Z-T1-recovered';p.mkdir(parents=True)
            (root/'state.json').write_text(json.dumps({'accepted':[{'run':p.name,'commit':'recovered-commit'}]}))
            since=dt.datetime(2026,10,31,tzinfo=dt.timezone.utc)
            report=collect([{'id':'fixture','state_dir':name}],since,since+dt.timedelta(days=1))['projects'][0]
            self.assertEqual(report['accepted'],1)
            self.assertEqual(report['details'][0]['commit'],'recovered-commit')
