import csv
import datetime as dt
import hashlib
import fcntl
import importlib.util
import io
import json
import signal
import sys
import tempfile
from pathlib import Path
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo
from contextlib import nullcontext

SPEC = importlib.util.spec_from_file_location("midnight_under_test", Path(__file__).resolve().parents[1] / "midnight.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
UTC = dt.timezone.utc
TZ = ZoneInfo("Australia/Adelaide")


def at(value):
    return dt.datetime.fromisoformat(value).replace(tzinfo=TZ).astimezone(UTC)


DATA = {"name": "Midnight", "timezone": "Australia/Adelaide", "schedule": {"start": "00:00", "end": "09:00"}}


class TimingTests(unittest.TestCase):
    def test_schedule_boundaries(self):
        for value, active in [("2026-10-31T23:59:59", False), ("2026-11-01T00:00", True),
                              ("2026-11-01T08:59:59", True), ("2026-11-01T09:00", False)]:
            self.assertEqual(M.window(at(value), DATA)["active"], active)

    def test_dst_tracks_wall_clock(self):
        for date, hours in [("2027-04-04", 10), ("2027-10-03", 8)]:
            instant = at(date + "T00:00")
            self.assertEqual((M.window(instant, DATA)["cutoff"] - instant).total_seconds(), hours * 3600)

    def test_override_expires_exactly_and_normal_schedule_returns(self):
        override = {"expires_at": at("2026-10-31T18:00").isoformat()}
        self.assertTrue(M.window(at("2026-10-31T17:59:59"), DATA, override)["active"])
        self.assertFalse(M.window(at("2026-10-31T18:00"), DATA, override)["active"])
        self.assertTrue(M.window(at("2026-11-01T00:00"), DATA, override)["active"])

    def test_override_expiring_inside_normal_window_continues_to_nine(self):
        override = {"expires_at": at("2026-11-01T06:00").isoformat()}
        for value in ("2026-11-01T05:00", "2026-11-01T06:00"):
            self.assertEqual(M.window(at(value), DATA, override)["cutoff"], at("2026-11-01T09:00"))

    def test_override_extends_normal_window(self):
        override = {"expires_at": at("2026-11-01T12:00").isoformat()}
        self.assertEqual(M.window(at("2026-11-01T08:00"), DATA, override)["cutoff"], at("2026-11-01T12:00"))

    def test_expiry_rejects_past_ambiguous_and_nonexistent_local_times(self):
        instant = at("2026-10-31T12:00")
        with self.assertRaises(ValueError): M.parse_expiry("2026-10-31 11:00", instant, "Australia/Adelaide")
        with self.assertRaises(ValueError): M.parse_expiry("2027-04-04 02:30", instant, "Australia/Adelaide")
        with self.assertRaises(ValueError): M.parse_expiry("2027-10-03 02:30", instant, "Australia/Adelaide")
        self.assertEqual(M.parse_expiry("2026-10-31 18:00", instant, "Australia/Adelaide"), at("2026-10-31T18:00"))


class IsolatedTests(unittest.TestCase):
    def test_satisfied_scope_wait_does_not_spin_and_new_direction_or_ready_work_wakes_it(self):
        instant=M.now();state={'done':['T1','T2']};M.write(Path(self.project['state_dir'])/'state.json',state)
        key=M.AW.fingerprint({'why':self.project['why'],'instruction':self.project['instruction'],'source':M.Q.policy()['project_backend_sha256']})
        M.write(Path(self.project['state_dir'])/'status.json',{'phase':'quality_wait','scope_wait':{'until':(instant+dt.timedelta(minutes=5)).isoformat(),'key':key}})
        self.assertFalse(M.eligible(self.project,instant))
        self.assertTrue(M.eligible(dict(self.project,instruction='New owner direction'),instant))
        M.write(Path(self.project['state_dir'])/'state.json',{'done':[]})
        self.assertTrue(M.eligible(self.project,instant))
        M.write(Path(self.project['state_dir'])/'state.json',state)
        self.assertTrue(M.eligible(self.project,instant+dt.timedelta(minutes=6)))

    def test_retired_repairs_are_history_and_never_queued_as_unfinished_work(self):
        value=M.queue(self.project,{'retired_repair_contracts':{'T1':{'reason':'Finding closed'}}})
        self.assertEqual(value['retired'],['T1']);self.assertEqual(value['completed'],0);self.assertEqual(value['next'],[])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="midnight-test-")
        self.root = Path(self.temp.name)
        self.state = self.root / "midnight-state"
        self.state.mkdir()
        self.policy = self.root / "OPEN_SOURCE.md"
        self.policy.write_text("Reuse suitable upstream components and retain provenance.\n")
        (self.root/'backend.py').write_text("import sys\nif sys.argv[1:] == ['recover']: sys.exit(0)\n")
        self.project = {"id": "example", "name": "Example", "on_schedule": True, "path": str(self.root),
                        "backend_protocol": 5,
                        "state_dir": str(self.root / "project-state"), "controller": "backend.py",
                        "config": "config.json", "backlog": "backlog.json", "brief": "ROADMAP.md",
                        "why": "Build a useful testing CLI.", "instruction": "Implement the next small task.", "local_model": "test"}
        self.data = dict(DATA, schema_version=1, projects=[self.project])
        self.real_validate_backend = M.validate_backend
        self.patches = [mock.patch.object(M, "STATE", self.state), mock.patch.object(M, "STOP", False),
                        mock.patch.object(M, "ENGINEERING_POLICY", self.policy),
                        mock.patch.object(M, "registry", return_value=self.data), mock.patch.object(M, "PYTHON", sys.executable),
                        mock.patch.object(M, "validate_backend"),
                        mock.patch.object(M.MM, 'task_selection',side_effect=lambda:nullcontext({'model':'fixture','digest':'f'*64,'context':32768})),
                        mock.patch.object(M, 'maintain_models'),mock.patch.object(M,'maintain_cloud')]
        for p in self.patches: p.start()
        M.write(self.root / "config.json", {"max_attempts_per_night": 24, "max_cloud_calls_per_night": 16,
                                           "max_failures_per_night": 8, "cloud_model": "test"})
        M.write(self.root / "backlog.json", [
            {"id": "T1", "title": "First"}, {"id": "T2", "title": "Second", "depends_on": ["T1"]}])

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def session(self):
        return {"id": "test-session", "date": "2026-10-31", "started_at": "2026-10-31T00:00:00+00:00",
                "mode": "override", "projects": {"example": {"accepted_runs": ["old-run"], "attempts": 1,
                                                             "why": self.project["why"]}}}

    def test_queue_respects_dependencies_and_exhaustion(self):
        self.assertEqual([t["id"] for t in M.queue(self.project, {})["next"]], ["T1"])
        self.assertEqual([t["id"] for t in M.queue(self.project, {"done": ["T1"]})["next"]], ["T2"])
        self.assertEqual(M.queue(self.project, {"tries": {"T1": 2}})["exhausted"], ["T1"])

    def test_restart_outside_window_retains_session_until_background_jobs_drain(self):
        M.write(self.state/'session.json',self.session())
        with mock.patch.object(M,'window',return_value={'active':False}),mock.patch.object(M,'stop_actions',return_value=False) as drain, \
             mock.patch.object(M,'finish') as finish,mock.patch.object(M,'reconcile_actions'),mock.patch.object(M,'recover_projects',return_value=[]):
            self.assertEqual(M.tick(),0)
        drain.assert_called_once();finish.assert_not_called()

    def test_midnight_rollover_keeps_old_jobs_live_and_attributes_both_sessions_once(self):
        old=self.session();old.update(id='old-session',durable_actions=True)
        current=self.session();current.update(id='new-session',durable_actions=True)
        store=M.AW.Actions(self.state)
        one=store.submit('example',{'session':old['id']},time.time()+60);store.claim(one['id'],M.PR.lease())
        store.charge(one['id'],'old-worker','local')
        M.queue_closing_session(old);M.write(self.state/'session.json',current)
        M.close_ready_sessions(M.registry());self.assertFalse(M.ledger_rows())
        store.finish(one['id'],'succeeded',{})
        two=store.submit('example',{'session':current['id']},time.time()+60);store.claim(two['id'],M.PR.lease())
        store.charge(two['id'],'new-worker','local');store.finish(two['id'],'succeeded',{})
        M.write(Path(self.project['state_dir'])/'state.json',{'accepted':[
            {'run':'old-accepted','id':'A','title':'Old accepted','commit':'a'*40,'at':'2026-10-31T01:00:00+00:00','session_id':old['id']},
            {'run':'new-accepted','id':'B','title':'New accepted','commit':'b'*40,'at':'2026-10-31T02:00:00+00:00','session_id':current['id']}],
            'attempts_today':99})
        M.close_ready_sessions(M.registry())
        self.assertEqual(M.read(self.state/'session.json')['id'],current['id'])
        M.finish(current,'window_ended',M.registry())
        rows=M.ledger_rows();self.assertEqual(len(rows),2)
        self.assertEqual({r['session_id']:int(r['accepted_count']) for r in rows},{old['id']:1,current['id']:1})
        self.assertTrue(all(int(r['attempt_count'])==1 for r in rows))

    def test_pause_during_model_maintenance_prevents_project_dispatch(self):
        M.write(self.state/'override.json',{'expires_at':(M.now()+dt.timedelta(hours=1)).isoformat()})
        def pause(*_args):(self.state/'PAUSED').touch()
        with mock.patch.object(M,'maintain_models',side_effect=pause),mock.patch.object(M,'submit_project') as dispatch, \
             mock.patch.object(M,'eligible',return_value=False),mock.patch.object(M.PR,'resource',return_value=nullcontext()):
            self.assertEqual(M.tick(),0)
        dispatch.assert_not_called()

    def test_repeated_ready_dispatch_keeps_the_controller_observation_current(self):
        M.write(self.state/'override.json',{'expires_at':(M.now()+dt.timedelta(hours=1)).isoformat()})
        observations=[]
        def ready(project):
            observations.append(M.read(self.state/'status.json'))
            if len(observations)==2:M.STOP=True
            return [project]
        with mock.patch.object(M,'eligible',return_value=True),mock.patch.object(M,'submit_project'), \
             mock.patch.object(M,'fair_projects',side_effect=lambda projects:ready(projects[0])), \
             mock.patch.object(M.time,'sleep'):
            self.assertEqual(M.tick(),0)
        self.assertEqual(len(observations),2)
        self.assertTrue(all(o.get('phase')=='waiting_for_project' for o in observations))
        self.assertTrue(all(o.get('code_sha256')==M.LOADED_CODE_SHA256 and M.PR.alive(o.get('process')) for o in observations))
        self.assertGreater(observations[1]['updated_at'],observations[0]['updated_at'])

    def test_running_last_attempt_is_not_labelled_exhausted(self):
        queue=M.queue(self.project,{'tries':{'T1':2},'active_attempt':{'task':{'id':'T1'}}})
        self.assertEqual(queue['exhausted'],[])
        self.assertEqual(queue['in_flight'],['T1'])
        self.assertEqual(queue['next'],[])

    def test_superseded_failure_is_retained_without_competing_with_repair(self):
        M.write(self.root/'backlog.json',[{'id':'T1','title':'Old','superseded_by':'R1'}, {'id':'R1','title':'Repair'}])
        queue=M.queue(self.project,{'tries':{'T1':2}})
        self.assertEqual(queue['superseded'],[{'id':'T1','by':'R1'}])
        self.assertEqual(queue['exhausted'],[])
        self.assertEqual(queue['next'],[{'id':'R1','title':'Repair'}])
        self.assertEqual(queue['completed'],0)

    def test_backend_admission_rejects_missing_or_stale_shared_quality(self):
        project=dict(self.project,backend_protocol=5)
        for value in ({}, {'backend_protocol':5,'worker_quality_policy':{},'recovery':'model-free'}):
            result=mock.Mock(returncode=0,stdout=json.dumps(value))
            with mock.patch.object(M.subprocess,'run',return_value=result):
                with self.assertRaises(ValueError):self.real_validate_backend(project)
        result=mock.Mock(returncode=0,stdout=json.dumps({'backend_protocol':5,'worker_quality_policy':M.Q.policy(),'worker_model_contract':M.Q.model_contract(),'quality_cycle_policy':M.QC.policy(),'cloud_policy':M.CP.policy(),'cloud_rescue_policy':M.CR.policy(),'git_delivery_policy':M.GD.policy(),'action_policy':M.AW.policy(),'recovery':'model-free'}))
        with mock.patch.object(M.subprocess,'run',return_value=result):self.real_validate_backend(project)
        for value in (None,1,True,2.0,4):
            with self.assertRaises(ValueError):self.real_validate_backend(dict(project,backend_protocol=value))

    def test_ledger_counts_only_new_acceptances_and_is_replay_safe(self):
        state = {"accepted": [{"run": "old-run", "title": "Earlier"}, {"run": "new-run", "title": "JSON export"}],
                 "tries": {"T1": 1, "T2": 2}, "pending": {"task": {"id": "T3"}}}
        M.write(Path(self.project["state_dir"]) / "state.json", state)
        session = self.session()
        M.finish(session, "window_ended", self.data)
        # Simulate crash after CSV replacement but before session checkpoint.
        M.finish(self.session(), "window_ended", self.data)
        rows = list(csv.DictReader(io.StringIO((self.state / "sessions.csv").read_text())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["accepted_count"], "1")
        self.assertEqual(rows[0]["attempt_count"], "2")
        self.assertIn("JSON export", rows[0]["summary"])
        self.assertNotIn("Earlier", rows[0]["achieved"])
        self.assertIn("pending", rows[0]["summary"])
        self.assertNotIn("\n", rows[0]["summary"])

    def test_committed_session_receipt_survives_crash_and_changed_recovery_reason(self):
        project_state = Path(self.project["state_dir"]) / "state.json"
        M.write(project_state, {"tries": {"T1": 3}, "accepted": [{"run": "new-run", "title": "Verified change"}]})
        M.write(self.state / "session.json", self.session())
        real_write = M.write
        def interrupted_checkpoint(path, value):
            if Path(path) == self.state / "sessions" / "test-session.json":
                raise OSError("Crash after ledger commit and before finished checkpoint")
            return real_write(path, value)
        with mock.patch.object(M, "write", side_effect=interrupted_checkpoint):
            with self.assertRaises(OSError):
                M.finish(self.session(), "controller_error", self.data)
        receipt = M.ledger_rows()[0]
        self.assertEqual(receipt["outcome"], "controller_error")
        self.assertFalse(M.read(self.state / "session.json").get("finished", False))
        # Later observations cannot be folded into a session already committed.
        M.write(project_state, {"tries": {"T1": 90}, "accepted": [{"run": "later-run", "title": "Later work"}]})
        resumed = M.read(self.state / "session.json")
        M.finish(resumed, "window_ended", self.data)
        self.assertEqual(M.ledger_rows(), [receipt])
        finished = M.read(self.state / "session.json")
        self.assertTrue(finished["finished"])
        self.assertEqual(finished["outcome"], "controller_error")
        self.assertEqual(finished["ended_at"], receipt["ended_at"])
        self.assertEqual(finished["rows"], [receipt])

    def test_failed_attempt_is_not_reported_as_an_achievement(self):
        M.write(Path(self.project["state_dir"]) / "state.json", {"tries": {"T1": 2}, "accepted": []})
        M.finish(self.session(), "controller_error", self.data)
        row = list(csv.DictReader(io.StringIO((self.state / "sessions.csv").read_text())))[0]
        self.assertEqual(row["accepted_count"], "0")
        self.assertIn("No new changes passed", row["summary"])

    def test_idle_session_still_records_a_zero_work_paragraph(self):
        session = M.begin_session(self.data, {"mode": "scheduled"})
        M.finish(session, "window_ended", self.data)
        row = M.ledger_rows()[0]
        self.assertEqual(row["attempt_count"], "0")
        self.assertEqual(row["accepted_count"], "0")
        self.assertIn("Midnight scheduled Example", row["summary"])
        self.assertNotIn("worked on", row["summary"])

    def test_missing_and_corrupt_state_are_visible(self):
        self.assertEqual(M.project_snapshot(self.project)["phase"], "unobserved")
        p = Path(self.project["state_dir"])
        p.mkdir()
        (p / "status.json").write_text("bad JSON")
        self.assertTrue(M.project_snapshot(self.project)["errors"])

    def test_accepted_tasks_do_not_show_old_rejections_as_current_feedback(self):
        M.write(Path(self.project["state_dir"]) / "state.json", {
            "done": ["T1"], "feedback": {"T1": "Earlier rejection now resolved", "T2": "Still needs a fix"}})
        self.assertEqual(M.project_snapshot(self.project)["recent_feedback"], {"T2": "Still needs a fix"})

    def test_expiry_stops_a_real_backend_and_retains_instruction_snapshot(self):
        (self.root / "backend.py").write_text("import time\ntime.sleep(60)\n")
        session = self.session()
        expiry = M.now() + dt.timedelta(seconds=1)
        M.write(self.state / "override.json", {"expires_at": expiry.isoformat()})
        start = time.monotonic()
        code = M.run_project(self.project, expiry, session)
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(code, -signal.SIGTERM)
        self.assertEqual(M.read(self.state / "instructions/example.json")["instruction"], self.project["instruction"])
        policy = M.read(self.state / "instructions/example.json")["engineering_policy"]
        self.assertEqual(policy["instruction"], self.policy.read_text())
        self.assertEqual(policy["sha256"], hashlib.sha256(self.policy.read_bytes()).hexdigest())
        self.assertEqual(M.read(self.state/'instructions/example.json')['worker_quality_policy'],M.Q.policy())
        self.assertFalse(M.read(self.state / "active-child.json"))

    def test_missing_standing_policy_prevents_backend_dispatch(self):
        self.policy.unlink()
        with mock.patch.object(M.subprocess, "Popen") as start:
            with self.assertRaises(FileNotFoundError):
                M.run_project(self.project, M.now() + dt.timedelta(minutes=1), self.session())
        start.assert_not_called()

    def test_restore_session_and_csv_then_replay_without_duplicates(self):
        M.write(Path(self.project['state_dir'])/'state.json', {'tries':{'T1':2},'accepted':[{'run':'new-run','title':'Verified fixture'}]})
        M.finish(self.session(),'window_ended',self.data)
        (self.state/'session.json').write_text('{truncated')
        (self.state/'sessions.csv').write_text('truncated csv')
        M.repair_controller_state()
        self.assertEqual(len(M.ledger_rows()),1)
        M.finish(self.session(),'window_ended',self.data)
        self.assertEqual(len(M.ledger_rows()),1)
        self.assertEqual(M.ledger_rows()[0]['accepted_count'],'1')

    def test_doctor_is_read_only_and_reports_repairable_mirror(self):
        M.write(self.state/'session.json',self.session())
        (self.state/'session.json').write_text('{truncated')
        with mock.patch.object(M,'power_observation',return_value={'filevault':'on'}), \
             mock.patch.object(M,'service',return_value={'loaded':True}), mock.patch.object(M.subprocess,'run') as start:
            result=M.doctor(self.data)
        self.assertEqual(result['status'],'repair_needed')
        self.assertEqual((self.state/'session.json').read_text(),'{truncated')
        start.assert_not_called()

    def test_recovery_refuses_to_compete_with_running_controller(self):
        with (self.state/'controller.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with mock.patch.object(M.subprocess,'run') as start:
                self.assertEqual(M.recover_runtime(self.data)['status'],'busy')
            start.assert_not_called()

    def test_unrecoverable_checkpoint_returns_attention_without_dispatch(self):
        M.write(self.state/'session.json',self.session())
        M.D.database(self.state/'session.json').write_bytes(b'corrupt database')
        with mock.patch.object(M.subprocess,'run') as start:
            self.assertEqual(M.recover_runtime(self.data)['status'],'needs_attention')
        start.assert_not_called()

    def test_cold_recovery_keeps_original_override_expiry(self):
        expiry=at('2026-10-31T18:00')
        M.write(self.state/'override.json',{'expires_at':expiry.isoformat()})
        (self.state/'override.json').write_text('{truncated')
        M.repair_controller_state()
        timing=M.window(at('2026-10-31T18:01'),self.data,M.read(self.state/'override.json'))
        self.assertFalse(timing['active'])

    def test_old_boot_lease_cannot_kill_unrelated_process(self):
        M.write(self.state/'active-child.json',{'pid':123,'stamp':'same-start','boot_id':'old-boot'})
        with mock.patch.object(M.D,'boot_id',return_value='new-boot'), \
             mock.patch.object(M,'stamp',return_value='same-start'), mock.patch.object(M.os,'kill') as kill, \
             mock.patch.object(M,'now',return_value=at('2026-10-31T12:00')):
            self.assertEqual(M.tick(),0)
        kill.assert_not_called()

    def test_cold_start_reconciles_projects_before_final_session_summary(self):
        statefile=Path(self.project['state_dir'])/'state.json';statefile.parent.mkdir()
        payload={'tries':{'T1':2},'accepted':[{'run':'new-run','title':'Commit completed before outage'}]}
        calls=self.root/'maintenance-call.txt'
        (self.root/'backend.py').write_text("import sys\nfrom pathlib import Path\n"
            +f"Path({str(calls)!r}).write_text(sys.argv[1])\n"
            +"if sys.argv[1:] == ['recover']:\n"
            +f" Path({str(statefile)!r}).write_text({json.dumps(payload)!r})\n"
            +" sys.exit(0)\nraise RuntimeError('A model cycle must not start outside the schedule')\n")
        M.write(self.state/'session.json',self.session())
        with mock.patch.object(M,'now',return_value=at('2026-10-31T12:00')):
            self.assertEqual(M.tick(),0)
        self.assertEqual(calls.read_text(),'recover')
        self.assertEqual(M.ledger_rows()[0]['accepted_count'],'1')

    def test_complete_dispatch_writes_session_csv_on_override_expiry(self):
        statefile = Path(self.project["state_dir"]) / "state.json"
        statefile.parent.mkdir()
        accepted = {"tries": {"T1": 1}, "accepted": [{"run": "synthetic-run", "title": "Synthetic fixture feature"}]}
        (self.root / "backend.py").write_text(
            "from pathlib import Path\nimport time,sys\nif sys.argv[1:] == ['recover']: sys.exit(0)\n" +
            f"Path({str(statefile)!r}).write_text({json.dumps(accepted)!r})\n" + "time.sleep(60)\n")
        M.write(self.state / "override.json", {"expires_at": (M.now() + dt.timedelta(seconds=2)).isoformat()})
        # Make this deterministic even if the test is invoked inside normal scheduled hours.
        fixed_daytime = at("2026-10-31T12:00")
        origin = M.now()
        expiry = fixed_daytime + dt.timedelta(seconds=2)
        M.write(self.state / "override.json", {"expires_at": expiry.isoformat()})
        with mock.patch.object(M, "now", side_effect=lambda: fixed_daytime + (dt.datetime.now(UTC) - origin)), \
             mock.patch.object(M,'submit_project',side_effect=M.run_project):
            self.assertEqual(M.tick(), 0)
        rows = M.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["accepted_count"], "1")
        self.assertEqual(rows[0]["attempt_count"], "1")
        self.assertEqual(rows[0]["outcome"], "window_ended")
        self.assertIn("Synthetic fixture feature", rows[0]["summary"])
        self.assertTrue(M.read(self.state / "session.json")["finished"])

    def test_long_override_splits_daily_work_without_double_counting(self):
        clock = [at("2026-10-31T23:59")]
        M.write(self.state / "override.json", {"expires_at": at("2026-11-01T10:00").isoformat()})
        statepath = Path(self.project["state_dir"]) / "state.json"
        calls = []
        def cycle(project, cutoff, session):
            state = M.read(statepath)
            session["projects"][project["id"]] = {"accepted_runs": [r["run"] for r in state.get("accepted", [])],
                "attempts": sum(state.get("tries", {}).values()), "why": project["why"], "project": project}
            calls.append(True)
            state.setdefault("accepted", []).append({"run": str(len(calls)), "title": "Daily feature"})
            state["tries"] = {"T1": len(calls)}
            M.write(statepath, state)
            clock[0] = at("2026-11-01T00:01" if len(calls) == 1 else "2026-11-01T10:00")
        with mock.patch.object(M, "now", side_effect=lambda: clock[0]), \
             mock.patch.object(M, "submit_project", side_effect=cycle), mock.patch.object(M.time, "sleep"):
            M.tick()
        rows = M.ledger_rows()
        self.assertEqual([r["date"] for r in rows], ["2026-10-31", "2026-11-01"])
        self.assertEqual([r["accepted_count"] for r in rows], ["1", "1"])
        self.assertEqual(rows[0]["outcome"], "daily_checkpoint")

    def test_pause_and_daily_allowances_make_project_ineligible(self):
        statepath = Path(self.project["state_dir"])
        M.write(statepath / "state.json", {"day": "2026-10-31", "attempts_today": 24})
        self.assertFalse(M.eligible(self.project, at("2026-10-31T01:00")))
        self.assertTrue(M.eligible(self.project, at("2026-11-01T01:00")))
        (statepath / "PAUSED").touch()
        self.assertFalse(M.eligible(self.project, at("2026-11-01T01:00")))

    def test_zero_cloud_budget_means_local_only_and_never_blocks_local_work(self):
        statepath = Path(self.project["state_dir"])
        config = dict(M.read(self.root / "config.json"), max_cloud_calls_per_night=0)
        M.write(self.root / "config.json", config)
        M.write(statepath / "state.json", {"day": "2026-10-31", "attempts_today": 1, "cloud_calls_today": 0})
        self.assertTrue(M.eligible(self.project, at("2026-10-31T01:00")))
        M.write(statepath / "state.json", {"day": "2026-10-31", "attempts_today": 24, "cloud_calls_today": 0})
        self.assertFalse(M.eligible(self.project, at("2026-10-31T01:00")))

    def test_continuous_override_lifts_volume_caps_then_restores_them(self):
        statepath = Path(self.project["state_dir"]) / "state.json"
        M.write(statepath, {"day": "2026-10-31", "attempts_today": 100, "cloud_calls_today": 100, "failures_today": 20})
        instant = at("2026-10-31T12:00")
        M.write(self.state / "override.json", {"expires_at": (instant + dt.timedelta(hours=1)).isoformat(), "limits": M.CONTINUOUS_LIMITS})
        self.assertTrue(M.eligible(self.project, instant))
        self.assertFalse(M.eligible(self.project, instant + dt.timedelta(hours=1)))
        self.assertEqual(M.read(self.root / "config.json")["max_cloud_calls_per_night"], 16)

    def test_temporary_limits_do_not_override_pause_or_cooldown(self):
        instant = at("2026-10-31T12:00")
        M.write(self.state / "override.json", {"expires_at": (instant + dt.timedelta(hours=1)).isoformat(), "limits": M.CONTINUOUS_LIMITS})
        statepath = Path(self.project["state_dir"])
        M.write(statepath / "state.json", {"cooldown_until": (instant + dt.timedelta(minutes=15)).isoformat()})
        self.assertFalse(M.eligible(self.project, instant))
        (statepath / "PAUSED").touch()
        self.assertFalse(M.eligible(self.project, instant + dt.timedelta(minutes=16)))

    def test_invalid_override_limits_fail_closed(self):
        instant = at("2026-10-31T12:00")
        for limits in ({"cloud_model": "other"}, {"max_cloud_calls_per_night": -1}, {"max_cloud_calls_per_night": True}):
            M.write(self.state / "override.json", {"expires_at": (instant + dt.timedelta(hours=1)).isoformat(), "limits": limits})
            with self.assertRaises(ValueError): M.effective_limits(instant)


if __name__ == "__main__":
    unittest.main()
