"""Fake/temp coverage for weekday closure and the single retained-action grant."""
import contextlib
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo
import action_store as A
import agent_discussion as AD
import commissioning as C
import guardian as G
import midnight as M
import project_action as PA
import schedule_control as S
import worker_quality as Q
from history_store import HistoryStore

TZ=ZoneInfo('Australia/Adelaide')
def at(value): return dt.datetime.fromisoformat(value).replace(tzinfo=TZ)
DATA={'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00','weekdays':['mon','tue','wed','thu','fri']}}


class WeekdayTests(unittest.TestCase):
    def test_weekend_next_start_and_exact_friday_closure(self):
        for stamp in ('2026-11-06T09:00','2026-11-07T04:00','2026-11-08T08:59:59'):
            value=S.window(at(stamp),DATA)
            self.assertFalse(value['active']);self.assertEqual(value['next_start'],at('2026-11-09T00:00'))
            self.assertFalse(G.window(DATA,{},at(stamp)))
        self.assertTrue(M.window(at('2026-11-06T08:59:59'),DATA)['active'])
        self.assertTrue(G.window(DATA,{},at('2026-11-09T00:00')))
        self.assertEqual(M.window(at('2026-11-09T00:00'),DATA)['cutoff'],at('2026-11-09T09:00'))

    def test_omitted_weekdays_preserves_sunday_dst_and_overrides(self):
        data={'timezone':DATA['timezone'],'schedule':{'start':'00:00','end':'09:00'}}
        for date,hours in (('2027-04-04',10),('2027-10-03',8)):
            start=at(date+'T00:00');value=S.window(start,data)
            self.assertTrue(value['active']);self.assertEqual(value['cutoff'].timestamp()-start.timestamp(),hours*3600)
        stamp=at('2026-11-07T12:00');expiry=stamp+dt.timedelta(hours=1)
        self.assertTrue(S.window(stamp,DATA,{'expires_at':expiry.isoformat()})['active'])
        self.assertFalse(S.window(expiry,DATA,{'expires_at':expiry.isoformat()})['active'])
        self.assertTrue(G.window(DATA,{'mode':'continuous','expires_at':None},stamp))

    def test_single_weekday_and_cross_midnight_are_start_date_bound(self):
        data=dict(DATA,schedule={'start':'23:00','end':'02:00','weekdays':['fri']})
        self.assertTrue(S.window(at('2026-11-07T01:00'),data)['active'])
        self.assertFalse(S.window(at('2026-11-07T02:00'),data)['active'])
        self.assertEqual(S.window(at('2026-11-06T23:30'),data)['next_start'],at('2026-11-13T23:00'))
        for days in ([],['mon','mon'],['monday'],[True],'mon'):
            with self.assertRaises(ValueError):S.window(at('2026-11-07T12:00'),dict(DATA,schedule=dict(DATA['schedule'],weekdays=days)))


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.runtime=self.root/'runtime';self.project={'id':'fixture','on_schedule':True,'state_dir':str(self.root/'project')}
        Path(self.project['state_dir']).mkdir();self.task={'id':'retained','title':'Retained repair'}
        self.save({'active_attempt':{'task':self.task}})
        self.data=dict(DATA,projects=[self.project]);self.instant=at('2026-11-07T14:00')
        self.end=self.instant+dt.timedelta(hours=9);self.model={'model':'fixture','digest':'b'*64,
            'context':32768,'bytes':1,'parameters_sha256':'c'*64,'promotion_generation':None}
        self.payload={'project':'fixture','worker_model':self.model,'source_head':'a'*40,'session':'fixture-session'}

    def save(self,state):
        (Path(self.project['state_dir'])/'state.json').write_text(json.dumps(state))

    def activate(self,**kw):
        return C.activate(self.runtime,self.data,'fixture','retained',self.end,'a'*40,self.model,'Owner commissioning',self.instant,**kw)

    def test_read_paths_do_not_create_state(self):
        self.assertFalse(C.active(self.runtime,self.instant));self.assertFalse(C.snapshot(self.runtime,self.instant)['blocks_dispatch'])
        self.assertFalse(self.runtime.exists())

    def test_atomic_one_use_reservation_survives_restart_and_terminal_replay(self):
        grant=self.activate();self.assertTrue(grant['active'])
        first=C.reserve(self.runtime,self.project,self.payload,self.instant)
        second=C.reserve(self.runtime,self.project,self.payload,self.instant+dt.timedelta(minutes=10))
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['deadline'],self.end.timestamp())
        self.assertTrue(C.permits(self.runtime,first,self.instant))
        A.Actions(self.runtime).finish(first['id'],'failed',{'reason':'unknown_launch'})
        replay=C.reserve(self.runtime,self.project,self.payload,self.instant+dt.timedelta(minutes=20))
        self.assertEqual(replay['id'],first['id']);self.assertEqual(replay['state'],'failed')
        current=C.snapshot(self.runtime,self.instant+dt.timedelta(minutes=20))
        self.assertFalse(current['active']);self.assertTrue(current['blocks_dispatch'])
        self.assertFalse(C.snapshot(self.runtime,at('2026-11-09T00:00'))['blocks_dispatch'])
        self.assertEqual(sum(e['kind']=='commissioning_consumed' for e in A.Actions(self.runtime).events()),1)
        with self.assertRaises(ValueError):self.activate()

    def test_deadline_foreign_job_model_source_and_task_changes_cannot_admit(self):
        self.activate()
        for payload in (dict(self.payload,source_head='c'*40),dict(self.payload,worker_model={'model':'other'})):
            with self.assertRaises(ValueError):C.reserve(self.runtime,self.project,payload,self.instant)
        self.save({'active_attempt':{'task':dict(self.task,title='Changed contract')}})
        with self.assertRaises(ValueError):C.reserve(self.runtime,self.project,self.payload,self.instant)
        self.save({'active_attempt':{'task':self.task}})
        job=C.reserve(self.runtime,self.project,self.payload,self.instant)
        self.assertFalse(C.permits(self.runtime,dict(job,id='foreign'),self.instant))
        self.assertFalse(C.permits(self.runtime,dict(job,deadline=job['deadline']+1),self.instant))
        self.assertFalse(C.permits(self.runtime,job,self.end))
        permitted,_,timing=PA.control(self.runtime,job['id'],self.data,self.instant,S.window)
        self.assertTrue(permitted);self.assertEqual(timing['cutoff'].timestamp(),self.end.timestamp())
        self.assertFalse(PA.control(self.runtime,job['id'],self.data,self.end,S.window)[0])

    def test_pause_and_installation_never_get_commissioning_exception(self):
        self.activate();job=C.reserve(self.runtime,self.project,self.payload,self.instant)
        for marker in (self.runtime/'PAUSED',self.runtime/'INSTALLING',self.runtime/'WEEKDAY_ACTIVATION_PENDING',Path(self.project['state_dir'])/'PAUSED'):
            marker.touch()
            self.assertFalse(C.permits(self.runtime,job,self.instant))
            self.assertFalse(PA.control(self.runtime,job['id'],self.data,self.instant,S.window)[0])
            marker.unlink()

    def test_activation_hold_can_arm_but_cannot_launch_or_bootstrap_routine_work(self):
        self.runtime.mkdir();marker=self.runtime/'WEEKDAY_ACTIVATION_PENDING';marker.touch()
        self.activate()
        with self.assertRaises(ValueError):C.reserve(self.runtime,self.project,self.payload,self.instant)
        with mock.patch.object(M,'STATE',self.runtime),mock.patch.object(M,'registry',return_value=self.data), \
             mock.patch.object(M,'publish') as publish,mock.patch.object(M,'recover_projects',side_effect=AssertionError('No backend')), \
             mock.patch.object(M,'STOP',False),mock.patch.object(M.time,'sleep',side_effect=lambda _:setattr(M,'STOP',True)), \
             mock.patch.object(AD.C.Workspace,'bootstrap',side_effect=AssertionError('No workspace writes')):
            self.assertEqual(M.tick(),0)
            self.assertIsNone(AD.schedule(self.root,self.data,self.instant,self.end,'session'))
            self.assertEqual(publish.call_args.args[0],'activation_pending')
        marker.unlink();job=C.reserve(self.runtime,self.project,self.payload,self.instant)
        self.assertTrue(C.permits(self.runtime,job,self.instant))

    def test_broken_link_activation_gate_still_holds_commissioning(self):
        self.activate()
        gate=self.runtime/'WEEKDAY_ACTIVATION_PENDING';gate.symlink_to(self.root/'absent-target')
        with self.assertRaisesRegex(ValueError,'paused or fenced'):
            C.reserve(self.runtime,self.project,self.payload,self.instant)

    def test_activation_is_bounded_and_does_not_displace_another_owned_job(self):
        self.end=self.instant+dt.timedelta(hours=9,seconds=1)
        with self.assertRaises(ValueError):self.activate()
        self.end=self.instant+dt.timedelta(hours=9)
        A.Actions(self.runtime).submit('other',{},time.time()+600)
        with self.assertRaises(ValueError):self.activate()
        self.assertFalse(C.snapshot(self.runtime,self.instant)['active'])

    def test_retained_manager_scope_without_active_attempt_is_bound(self):
        self.save({});scope={'id':'s'*64,'project':'fixture','episode':'issue:1','task_hash':Q.task_fingerprint(self.task),
                            'stage':'micro-3','head':'c'*40,'profile':'profile'}
        with HistoryStore(Path(self.project['state_dir'])).transaction() as con:
            con.execute('CREATE TABLE mr_plans(project TEXT,episode TEXT,payload TEXT)')
            con.execute('CREATE TABLE mr_completed(project TEXT,episode TEXT)')
            con.execute('INSERT INTO mr_plans VALUES(?,?,?)',('fixture','issue:1',json.dumps({'task':self.task})))
            con.execute('CREATE TABLE pr_scopes(id TEXT,project TEXT,episode TEXT,task_hash TEXT,stage TEXT,head TEXT,profile TEXT,payload TEXT)')
            con.execute('INSERT INTO pr_scopes VALUES(?,?,?,?,?,?,?,?)',(*scope.values(),json.dumps({'task_id':'retained'})))
        with self.assertRaises(ValueError):self.activate()
        self.activate(scope_id=scope['id']);job=C.reserve(self.runtime,self.project,self.payload,self.instant)
        self.assertEqual(job['payload']['commissioning']['recovery_scope'],scope)
        self.assertTrue(C.permits(self.runtime,job,self.instant))

    def test_commission_dispatch_starts_one_action_and_no_routine_work(self):
        self.activate();session={'id':'fixture-session','commissioning':C.snapshot(self.runtime,self.instant)['id']}
        self.runtime.mkdir(exist_ok=True);(self.runtime/'session.json').write_text(json.dumps(session))
        def launch(runtime,project,descriptor,brief,session_id,cutoff,**kwargs):
            self.assertTrue(kwargs['commissioning'])
            job=C.reserve(runtime,project,self.payload,self.instant);A.Actions(runtime).finish(job['id'],'succeeded',{})
            return job['id']
        with mock.patch.object(M,'STATE',self.runtime),mock.patch.object(M,'now',return_value=self.instant),mock.patch.object(M,'STOP',False), \
             mock.patch.object(M.MM,'task_selection',return_value=contextlib.nullcontext(self.model)),mock.patch.object(M,'project_brief',return_value={}), \
             mock.patch.object(M.PA,'launch',side_effect=launch) as launched,mock.patch.object(M,'reconcile_actions'), \
             mock.patch.object(M,'finish'),mock.patch.object(M,'publish'),mock.patch.object(M.subprocess,'Popen'),mock.patch.object(M.time,'sleep'), \
             mock.patch.object(M,'routine_maintenance',side_effect=AssertionError('No maintenance')),mock.patch.object(AD,'schedule',side_effect=AssertionError('No discussion')):
            self.assertEqual(M.run_commissioning(self.data),0);self.assertEqual(M.run_commissioning(self.data),0)
        self.assertEqual(launched.call_count,1)

    def test_window_drain_preserves_only_exact_metadata_identity_and_pause_cancels_it(self):
        store=A.Actions(self.runtime)
        review=store.submit('midnight-performance-review',{'kind':'performance_review'},time.time()+600)
        spoof=store.submit('midnight',{'kind':'performance_review'},time.time()+600)
        product=store.submit('fixture',{},time.time()+600)
        with mock.patch.object(M,'STATE',self.runtime):
            self.assertTrue(M.stop_actions(schedule_only=True))
            self.assertEqual([j['id'] for j in store.jobs()],[review['id']])
            self.assertEqual(store.get(spoof['id'])['state'],'interrupted')
            self.assertEqual(store.get(product['id'])['state'],'interrupted')
            (self.runtime/'PAUSED').touch();self.assertTrue(M.stop_actions(schedule_only=True))
            self.assertFalse(store.jobs())

    def test_installation_hold_cancels_performance_review_despite_window_exemption(self):
        store=A.Actions(self.runtime)
        store.submit('midnight-performance-review',{'kind':'performance_review'},time.time()+600)
        (self.runtime/'WEEKDAY_ACTIVATION_PENDING').touch()
        with mock.patch.object(M,'STATE',self.runtime):self.assertTrue(M.stop_actions(schedule_only=True))
        self.assertFalse(store.jobs())

    def test_product_action_control_stops_at_nine_without_affecting_saved_history(self):
        stamp=at('2026-11-09T08:59:59');store=A.Actions(self.runtime,clock=stamp.timestamp)
        job=store.submit('fixture',self.payload,stamp.timestamp()+3600)
        self.assertTrue(PA.control(self.runtime,job['id'],self.data,stamp,S.window)[0])
        self.assertFalse(PA.control(self.runtime,job['id'],self.data,stamp+dt.timedelta(seconds=1),S.window)[0])
        self.assertEqual(store.get(job['id'])['deadline'],stamp.timestamp()+3600)

    def test_scrum_is_deferred_without_dropping_pending_question(self):
        workspace=mock.Mock();workspace.ensure_scrum.return_value=None
        store=A.Actions(self.runtime,clock=self.instant.timestamp)
        scrum={'id':'scrum','kind':'scrum','actor':'worker:fixture'}
        question={'id':'question','kind':'question','actor':'planner:fixture'}
        workspace.pending_work.return_value=[scrum,question]
        with mock.patch.object(AD.C,'Workspace',return_value=workspace),mock.patch.object(AD,'recover_claims'), \
             mock.patch.object(AD,'Actions',return_value=store),mock.patch.object(AD.time,'time',return_value=self.instant.timestamp()), \
             mock.patch.object(AD.P,'source_lock',return_value=contextlib.nullcontext()),mock.patch.object(AD.PA,'source_head',return_value='a'*40), \
             mock.patch.object(M.MM,'task_selection',return_value=contextlib.nullcontext(self.model)),mock.patch.object(AD.PA,'spawn_pending'):
            job_id=AD.schedule(self.root,self.data,self.instant,self.end,'session',product_work=True)
        job=A.Actions(self.runtime).get(job_id)
        self.assertEqual(job['payload']['work'],'question')
        workspace.fail_work.assert_not_called();workspace.finish_work.assert_not_called();workspace.claim_work.assert_not_called()


if __name__=='__main__':unittest.main()
