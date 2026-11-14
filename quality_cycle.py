"""Project-wide quality admission and evidence-backed continuous improvement.

This module makes decisions; it never executes project commands, calls a model,
or promotes a commit. The host owns those effects and supplies saved receipts.
"""
import hashlib
import json
from pathlib import Path
import time
import uuid

from history_store import HistoryStore, bounded_limit, encoded, timestamp

API_VERSION = 1
KINDS = ('baseline', 'after_accepted', 'daily', 'weekly', 'monthly')
PASS = ('passed', 'no_change')
OUTCOMES = PASS + ('failed', 'incomplete', 'unavailable')
INTERVALS = {'daily': 86400, 'weekly': 7 * 86400, 'monthly': 30 * 86400}
INSTRUCTION = ('Project-wide material defects and technical debt block new features. '
               'Independent repairs remain eligible. Require current canonical-head quality '
               'assessment and coordinator-owned versioned profile. Preserve stable findings '
               'and issue-episode lineage. Feature admission is one-use and source/profile/'
               'findings-bound, revalidated immediately before promotion. Attempts and '
               'assessments are never accepted achievements. Keep daily, 7-day and 30-day '
               'assessments and indexed durable evidence for every project.')


def policy():
    return {'id': 'midnight-continuous-quality', 'version': API_VERSION,
            'instruction': INSTRUCTION,
            'sha256': hashlib.sha256(INSTRUCTION.encode()).hexdigest(),
            'module_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'history_store_sha256': hashlib.sha256(Path(__file__).with_name('history_store.py').read_bytes()).hexdigest(),
            'profile_manager_sha256': hashlib.sha256(Path(__file__).with_name('profile_manager.py').read_bytes()).hexdigest()}


class QualityGateError(ValueError):
    pass


def profile_hash(profile):
    return hashlib.sha256(encoded(profile).encode()).hexdigest()


def load_profile(profile):
    if isinstance(profile, (str, Path)):
        profile = json.loads(Path(profile).read_text())
    # Defensive copy prevents a worker mutating the coordinator's live profile.
    profile = json.loads(encoded(profile))
    if profile.get('schema_version') != 1 or type(profile.get('version')) is not int or profile['version'] < 1:
        raise ValueError('A versioned quality profile is required')
    if not profile.get('id') or profile.get('owner') != 'coordinator':
        raise ValueError('Quality profiles must be coordinator-owned')
    for section in ('capabilities', 'contracts', 'checks', 'workflows', 'metrics'):
        if not isinstance(profile.get(section), list):
            raise ValueError('Quality profile requires ' + section)
    checks = profile['checks']
    ids = [item.get('id') for item in checks]
    if not checks or not all(isinstance(item, str) and item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError('Quality profile requires uniquely identified checks')
    if not any(item.get('required', True) for item in checks):
        raise ValueError('Quality profile must have a required check')
    for workflow in profile['workflows']:
        if not workflow.get('id') or not set(workflow.get('check_ids', [])).issubset(ids):
            raise ValueError('Workflow references an unknown check')
    for kind, interval in profile.get('assessment_intervals', {}).items():
        if kind not in INTERVALS or type(interval) is not int or interval <= 0:
            raise ValueError('Invalid assessment interval')
    if type(profile.get('freshness_seconds', 86400)) is not int or profile.get('freshness_seconds', 86400) <= 0:
        raise ValueError('Invalid quality freshness')
    return profile


class QualityCycle:
    def __init__(self, runtime, project_id, profile, *, clock=None):
        if not isinstance(project_id, str) or not project_id:
            raise ValueError('Project identity is required')
        self.store = HistoryStore(runtime)
        self.project_id = project_id
        self.profile = load_profile(profile)
        if self.profile.get('project_id', project_id) != project_id:
            raise ValueError('Quality profile belongs to another project')
        self.profile_hash = profile_hash(self.profile)
        self.clock = clock or time.time

    def _project(self, con):
        con.execute('INSERT OR IGNORE INTO qc_project(project_id) VALUES(?)', (self.project_id,))

    def _revision(self, con):
        if con is None:
            return 0
        row = con.execute('SELECT findings_revision FROM qc_project WHERE project_id=?', (self.project_id,)).fetchone()
        return row[0] if row else 0

    def _bump(self, con):
        self._project(con)
        con.execute('UPDATE qc_project SET findings_revision=findings_revision+1 WHERE project_id=?', (self.project_id,))

    def _finding(self, con, finding_id):
        row = con.execute('SELECT * FROM qc_findings WHERE project_id=? AND finding_id=?',
                          (self.project_id, finding_id)).fetchone() if con is not None else None
        return json.loads(row['payload']) if row else None

    def get_finding(self, finding_id):
        with self.store.reader() as con:
            return self._finding(con, finding_id)

    def findings(self, *, state=None, material=None, limit=100, offset=0):
        bounded_limit(limit)
        if type(offset) is not int or offset < 0:
            raise ValueError('Invalid finding offset')
        clauses, args = ['project_id=?'], [self.project_id]
        if state is not None:
            clauses.append('state=?'); args.append(state)
        if material is not None:
            clauses.append('material=?'); args.append(int(bool(material)))
        with self.store.reader() as con:
            rows = [] if con is None else con.execute('SELECT payload FROM qc_findings WHERE ' +
                ' AND '.join(clauses) + ' ORDER BY updated_at DESC,finding_id LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _upsert_finding(self, con, finding):
        finding = json.loads(encoded(finding))
        finding_id = finding.get('id') or finding.get('finding_id')
        if not finding_id:
            key = finding.get('key')
            if not isinstance(key, str) or not key:
                raise ValueError('Finding requires a stable id or key, independent of task title')
            finding_id = 'F-' + hashlib.sha256(key.encode()).hexdigest()[:20]
        prior = self._finding(con, finding_id)
        if prior and prior['state'] in {'resolved','invalidated'}:
            raise QualityGateError('Use reopen_finding with regression evidence for a resolved finding')
        if not finding.get('evidence') and not (prior and prior.get('evidence')):
            raise ValueError('A finding requires observed evidence')
        # Severity/debt overrides survive a task rename and never silently close.
        result = dict(prior or {}, **finding)
        result.update(id=finding_id, state='open', episode=(prior or {}).get('episode', 1))
        result['episode_id'] = f"{finding_id}:{result['episode']}"
        result['material'] = bool(result.get('material', True))
        if prior and prior.get('material') and not result['material']:
            # An assessment may refine evidence, but cannot erase an outstanding
            # material blocker without the same proof required for resolution.
            result['material'] = True
            result['severity'] = prior.get('severity', 'major')
        result.setdefault('category', 'defect')
        result.setdefault('severity', 'major' if result['material'] else 'minor')
        result['updated_at'] = self.clock()
        # Identical observations retain the revision and existing evidence date.
        comparison = lambda x: {k: v for k, v in (x or {}).items() if k != 'updated_at'}
        if prior and comparison(prior) == comparison(result):
            return prior
        con.execute('INSERT INTO qc_findings VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,finding_id) DO UPDATE SET episode=excluded.episode,state=excluded.state,material=excluded.material,updated_at=excluded.updated_at,payload=excluded.payload',
                    (self.project_id, finding_id, result['episode'], 'open', int(result['material']), result['updated_at'], encoded(result)))
        self._bump(con)
        self.store.append('finding_observed', self.project_id, result, episode_id=result['episode_id'],
                          occurred_at=self.clock(), connection=con)
        return result

    def upsert_finding(self, finding):
        with self.store.transaction() as con:
            return self._upsert_finding(con, finding)

    def resolve_finding(self, finding_id, evidence):
        """Closure requires host checks, independent review and an accepted commit."""
        if not isinstance(evidence, dict) or not evidence.get('accepted_commit') or evidence.get('checks_passed') is not True or evidence.get('review_approved') is not True:
            raise QualityGateError('Finding closure needs accepted commit, host checks and independent review evidence')
        with self.store.transaction() as con:
            finding = self._finding(con, finding_id)
            if finding is None:
                raise ValueError('Unknown finding')
            if finding['state'] == 'resolved':
                if finding.get('resolution') != evidence:
                    raise ValueError('Conflicting finding closure replay')
                return finding
            finding.update(state='resolved', resolution=evidence, updated_at=self.clock())
            con.execute('UPDATE qc_findings SET state=?,updated_at=?,payload=? WHERE project_id=? AND finding_id=?',
                        ('resolved', finding['updated_at'], encoded(finding), self.project_id, finding_id))
            self._bump(con)
            self.store.append('finding_resolved', self.project_id, finding, episode_id=finding['episode_id'],
                              occurred_at=self.clock(), connection=con)
            return finding

    def reopen_finding(self, finding_id, evidence):
        if not isinstance(evidence, dict) or not evidence.get('head') or not evidence.get('regression'):
            raise QualityGateError('A new issue episode requires demonstrated regression evidence')
        with self.store.transaction() as con:
            finding = self._finding(con, finding_id)
            if not finding or finding['state'] not in {'resolved','invalidated'}:
                raise QualityGateError('Only a verified closed finding can start a new episode')
            prior_episode = finding['episode_id']
            finding.update(state='open', episode=finding['episode'] + 1, evidence=evidence, updated_at=self.clock())
            finding['episode_id'] = f"{finding_id}:{finding['episode']}"
            finding['previous_episode_id'] = prior_episode
            finding.pop('resolution', None)
            finding.pop('invalidation', None)
            con.execute('UPDATE qc_findings SET episode=?,state=?,updated_at=?,payload=? WHERE project_id=? AND finding_id=?',
                        (finding['episode'], 'open', finding['updated_at'], encoded(finding), self.project_id, finding_id))
            self._bump(con)
            self.store.append('finding_reopened', self.project_id, finding, episode_id=finding['episode_id'],
                              occurred_at=self.clock(), connection=con)
            return finding

    def _observation_stamp(self,con,episode):
        rows=con.execute("SELECT sequence,event_id,kind,payload FROM qc_events WHERE project_id=? AND episode_id=? AND kind IN ('finding_observed','finding_reopened') ORDER BY sequence LIMIT 501",(self.project_id,episode)).fetchall()
        if len(rows)>500:raise QualityGateError('Observation set exceeds bounded correction review')
        return {'sha256':hashlib.sha256(encoded([dict(r) for r in rows]).encode()).hexdigest(),
                'count':len(rows),'last_sequence':rows[-1]['sequence'] if rows else None}

    def finding_observation_stamp(self,finding_id):
        with self.store.reader() as con:
            finding=self._finding(con,finding_id)
            if not finding:raise QualityGateError('Unknown finding')
            return self._observation_stamp(con,finding['episode_id'])

    def invalidate_host_finding(self,finding_id,expected_episode,expected_evidence_hash,correction):
        """Explicit reviewed correction of a host-created report, never a repair.

        The host must inspect *all* creation/update observations in this episode.
        A newer general pass alone cannot invalidate a real product assertion.
        Original events, episode and rescue charges remain unchanged.
        """
        if (not finding_id.startswith('host-check-') or correction.get('classification')!='host_orchestration_error'
                or correction.get('independent_review_approved') is not True
                or correction.get('all_episode_observations_checked') is not True
                or not correction.get('observations') or not correction.get('same_source_pass')
                or not correction.get('review_receipt_sha256') or not correction.get('observation_stamp')):
            raise QualityGateError('Host finding correction requires complete provenance and independent review')
        with self.store.transaction() as con:
            finding=self._finding(con,finding_id)
            if finding and finding.get('state')=='invalidated' and finding.get('invalidation')==correction:return finding
            if (not finding or finding['state']!='open' or finding['episode_id']!=expected_episode
                    or hashlib.sha256(encoded(finding.get('evidence')).encode()).hexdigest()!=expected_evidence_hash
                    or self._observation_stamp(con,expected_episode)!=correction['observation_stamp']):
                raise QualityGateError('Host finding episode or evidence changed before correction')
            finding.update(state='invalidated',invalidation=correction,updated_at=self.clock())
            con.execute('UPDATE qc_findings SET state=?,updated_at=?,payload=? WHERE project_id=? AND finding_id=?',
                ('invalidated',finding['updated_at'],encoded(finding),self.project_id,finding_id))
            self._bump(con)
            self.store.append('finding_invalidated',self.project_id,finding,episode_id=expected_episode,
                occurred_at=self.clock(),connection=con)
            return finding

    def admit_task(self, task, *, source_task=None):
        """Coordinator metadata binds renamed/split retries to the same episode."""
        task = json.loads(encoded(task))
        task_id = task.get('id')
        if not isinstance(task_id, str) or not task_id:
            raise ValueError('Task id is required')
        kind = task.get('kind', 'feature')
        if kind not in ('feature', 'repair', 'maintenance', 'research'):
            raise ValueError('Unknown task kind')
        with self.store.transaction() as con:
            row = con.execute('SELECT payload FROM qc_task_lineage WHERE project_id=? AND task_id=?', (self.project_id, task_id)).fetchone()
            prior = json.loads(row[0]) if row else None
            source_id = source_task or task.get('retry_of', {}).get('task_id') or task.get('supersedes')
            source = con.execute('SELECT payload FROM qc_task_lineage WHERE project_id=? AND task_id=?', (self.project_id, source_id)).fetchone() if source_id else None
            inherited = json.loads(source[0]) if source else None
            finding_ids = task.get('finding_ids', [])
            finding = self._finding(con, finding_ids[0]) if finding_ids else None
            basis = prior or inherited or finding or {}
            issue_id = basis.get('issue_id') or (finding or {}).get('id') or task.get('issue_id') or task_id
            episode_id = basis.get('episode_id') or task.get('episode_id') or f'{issue_id}:1'
            if task.get('episode_id', episode_id) != episode_id or task.get('issue_id', issue_id) != issue_id:
                raise QualityGateError('Task rename cannot reset issue lineage')
            if kind == 'repair' and not finding_ids and not (inherited or {}).get('finding_ids'):
                raise QualityGateError('A shipped-product repair must bind an observed finding')
            for fid in finding_ids:
                if self._finding(con, fid) is None:
                    raise QualityGateError('Repair references an unknown finding')
            result = dict(task, kind=kind, issue_id=issue_id, episode_id=episode_id,
                          finding_ids=finding_ids or (inherited or {}).get('finding_ids', []))
            if inherited:
                result['lineage_parent'] = source_id
            con.execute('INSERT INTO qc_task_lineage VALUES(?,?,?,?,?) ON CONFLICT(project_id,task_id) DO UPDATE SET payload=excluded.payload',
                        (self.project_id, task_id, issue_id, episode_id, encoded(result)))
            return result

    def record_assessment(self, head, kind, outcome, evidence, checks, findings=None, metrics=None, *, assessment_id=None, assessed_at=None):
        if not head or kind not in KINDS or outcome not in OUTCOMES:
            raise ValueError('Invalid assessment identity, kind or outcome')
        if not isinstance(checks, list) or not isinstance(evidence, (dict, list, str)) or not evidence:
            raise ValueError('Assessments require saved evidence and check receipts')
        by_id = {check.get('id'): check for check in checks}
        if len(by_id) != len(checks):
            raise ValueError('Duplicate assessment check')
        required = [check['id'] for check in self.profile['checks'] if check.get('required', True)]
        all_passed = all(by_id.get(cid, {}).get('status') == 'passed' and by_id[cid].get('evidence') for cid in required)
        if outcome in PASS and not all_passed:
            raise QualityGateError('Passing assessment requires every required check and evidence')
        assessment_id = assessment_id or uuid.uuid4().hex
        now = timestamp(assessed_at) if assessed_at is not None else self.clock()
        if now > self.clock() + 5:
            raise ValueError('Assessment cannot be dated in the future')
        with self.store.transaction() as con:
            findings_hash = hashlib.sha256(encoded(findings or []).encode()).hexdigest()
            prior = con.execute('SELECT payload FROM qc_assessments WHERE assessment_id=?', (assessment_id,)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                values = {'project_id': self.project_id, 'head': head, 'kind': kind, 'outcome': outcome,
                          'profile_hash': self.profile_hash, 'checks': checks, 'evidence': evidence,
                          'metrics': metrics or {}, 'reported_findings_sha256': findings_hash}
                if any(saved.get(key) != value for key, value in values.items()) or (assessed_at is not None and saved['assessed_at'] != now):
                    raise ValueError('Conflicting assessment replay')
                return saved
            for finding in findings or []:
                self._upsert_finding(con, finding)
            result = {'id': assessment_id, 'project_id': self.project_id, 'head': head,
                      'profile_hash': self.profile_hash, 'profile_version': self.profile['version'],
                      'kind': kind, 'outcome': outcome, 'evidence': evidence, 'checks': checks,
                      'metrics': metrics or {}, 'assessed_at': now,
                      'reported_findings_sha256': findings_hash,
                      'findings_revision': self._revision(con), 'accepted_achievement': False}
            con.execute('INSERT INTO qc_assessments(assessment_id,project_id,head,profile_hash,kind,outcome,assessed_at,findings_revision,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                        (assessment_id, self.project_id, head, self.profile_hash, kind, outcome, now, result['findings_revision'], encoded(result)))
            self.store.append('assessment', self.project_id, result, event_id='assessment:' + assessment_id,
                              occurred_at=now, connection=con)
            return result

    def _latest(self, con, *, kind=None, head=None):
        if con is None:
            return None
        clauses, args = ['project_id=?', 'profile_hash=?'], [self.project_id, self.profile_hash]
        for column, value in [('kind', kind), ('head', head)]:
            if value is not None:
                clauses.append(column + '=?'); args.append(value)
        row = con.execute('SELECT payload FROM qc_assessments WHERE ' + ' AND '.join(clauses) +
                          ' ORDER BY sequence DESC LIMIT 1', args).fetchone()
        return json.loads(row[0]) if row else None

    def _status(self, con, head):
        now, reasons, due = self.clock(), [], []
        revision = self._revision(con)
        baseline = self._latest(con, kind='baseline')
        current = self._latest(con, head=head)
        latest = {kind: self._latest(con, kind=kind) for kind in KINDS}
        if not baseline or baseline['outcome'] not in PASS:
            due.append('baseline'); reasons.append('missing_or_failed_baseline')
        if not current:
            if baseline:
                due.append('after_accepted')
            reasons.append('canonical_head_unassessed')
        elif current['outcome'] not in PASS:
            reasons.append('latest_assessment_' + current['outcome'])
            due.append(current['kind'])
        elif current['assessed_at'] + self.profile.get('freshness_seconds', 86400) <= now:
            reasons.append('assessment_stale'); due.append('daily')
        elif current['findings_revision'] != revision:
            reasons.append('findings_changed_since_assessment'); due.append('after_accepted')
        intervals = dict(INTERVALS, **self.profile.get('assessment_intervals', {}))
        for kind, interval in intervals.items():
            assessment = latest[kind] or baseline
            if assessment and (assessment['outcome'] not in PASS or assessment['assessed_at'] + interval <= now):
                due.append(kind); reasons.append(kind + '_assessment_due')
        blockers, count = [], 0
        if con is not None:
            count = con.execute("SELECT COUNT(*) FROM qc_findings WHERE project_id=? AND state='open' AND material=1", (self.project_id,)).fetchone()[0]
            rows = con.execute("SELECT payload FROM qc_findings WHERE project_id=? AND state='open' AND material=1 ORDER BY updated_at DESC,finding_id LIMIT 100", (self.project_id,)).fetchall()
            blockers = [json.loads(row[0]) for row in rows]
        if count:
            reasons.append('material_findings_open')
        from profile_manager import status as profile_status
        profile_admission = profile_status(self.store.path.parent, self.project_id, connection=con)
        if profile_admission['feature_hold']:
            reasons.append('profile_update_required')
        if profile_admission['profile_hash'] and profile_admission['profile_hash'] != self.profile_hash:
            reasons.append('effective_profile_changed')
        return {'project_id': self.project_id, 'head': head, 'profile_id': self.profile['id'],
                'profile_version': self.profile['version'], 'profile_hash': self.profile_hash,
                'findings_revision': revision, 'feature_allowed': not reasons,
                'reasons': list(dict.fromkeys(reasons)), 'assessment_due': list(dict.fromkeys(due)),
                'latest_assessment': current, 'assessments': latest, 'material_findings': blockers,
                'material_finding_count': count, 'material_findings_truncated': count > len(blockers),
                'profile_admission': profile_admission}

    def status(self, head):
        with self.store.reader() as con:
            return self._status(con, head)

    def next_decision(self, head, tasks=None, *, local_available=True, cloud_available=False):
        """Choose useful repair before assessment, then features after the gate.

        Cloud availability alone is never rescue authority. The caller supplies
        rescue_eligible only after the shared cloud-rescue policy qualifies it.
        """
        status = self.status(head)
        tasks = tasks or []
        runnable = [t for t in tasks if not t.get('done') and not t.get('superseded_by') and not t.get('blocked')]
        def decision(action, reason, **extra):
            result = dict(action=action, reason=reason, project_id=self.project_id, head=head,
                          profile_hash=self.profile_hash, findings_revision=status['findings_revision'], **extra)
            self.store.append('decision', self.project_id, result, task_id=extra.get('task', {}).get('id'), occurred_at=self.clock())
            if reason == 'no_useful_work':
                # This is a scheduler assessment, not a fresh execution of QA.
                # It cannot refresh the source-health assessment or clear debt.
                self.store.append('idle_assessment', self.project_id,
                    {'head': head, 'outcome': 'no_useful_work', 'accepted_achievement': False,
                     'profile_hash': self.profile_hash, 'findings_revision': status['findings_revision'],
                     'last_quality_assessment_id': (status['latest_assessment'] or {}).get('id'),
                     'evidence': 'No eligible outstanding task in the supplied host task set'},
                    occurred_at=self.clock())
            return result
        for task in runnable:
            if task.get('kind') not in ('repair', 'maintenance'):
                continue
            bindings = [self.get_finding(fid) for fid in task.get('finding_ids', [])]
            if not bindings or any(f is None for f in bindings):
                continue
            if not any(f['state'] == 'open' for f in bindings):
                continue
            if task.get('needs_manager'):
                continue
            if not task.get('exhausted') and local_available:
                return decision('implement_locally', 'repair_open_finding', task=task)
            if task.get('rescue_eligible') and cloud_available:
                return decision('cloud_rescue', 'qualified_repair_rescue', task=task)
        if status['assessment_due']:
            return decision('assess', 'quality_assessment_required', assessment_kind=status['assessment_due'][0],
                            reasons=status['reasons'])
        for task in runnable:
            if task.get('kind') not in ('maintenance', 'research') or task.get('needs_manager'):
                continue
            if not task.get('exhausted') and local_available:
                return decision('implement_locally', 'independent_upkeep_or_research', task=task)
            if task.get('rescue_eligible') and cloud_available:
                return decision('cloud_rescue', 'qualified_task_rescue', task=task)
        if status['material_finding_count']:
            return decision('needs_manager', 'material_findings_have_no_available_repair', reasons=status['reasons'])
        for task in runnable:
            if task.get('kind') == 'repair' or task.get('needs_manager'):
                continue
            if task.get('kind', 'feature') == 'feature' and not status['feature_allowed']:
                continue
            if not task.get('exhausted') and local_available:
                return decision('implement_locally', 'eligible_task', task=task)
            if task.get('rescue_eligible') and cloud_available:
                return decision('cloud_rescue', 'qualified_task_rescue', task=task)
        manager_tasks = [task['id'] for task in runnable if task.get('needs_manager')]
        if manager_tasks:
            return decision('needs_manager', 'issue_episodes_require_manager', task_ids=manager_tasks)
        return decision('wait', 'no_useful_work' if not runnable else 'eligible_worker_or_manager_unavailable',
                        accepted_achievement=False, reasons=status['reasons'])

    def issue_feature_ticket(self, head, task_id, *, ttl_seconds=3600):
        if not head or not task_id or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError('Feature ticket requires source, task and bounded lifetime')
        with self.store.transaction() as con:
            status = self._status(con, head)
            if not status['feature_allowed']:
                raise QualityGateError('Feature admission blocked: ' + ', '.join(status['reasons']))
            now = self.clock()
            ticket = {'id': uuid.uuid4().hex, 'project_id': self.project_id, 'task_id': task_id,
                      'head': head, 'profile_hash': self.profile_hash, 'findings_revision': status['findings_revision'],
                      'assessment_id': status['latest_assessment']['id'], 'issued_at': now,
                      'expires_at': min(now + ttl_seconds, status['latest_assessment']['assessed_at'] + self.profile.get('freshness_seconds', 86400))}
            con.execute('INSERT INTO qc_tickets VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (ticket['id'], self.project_id, task_id, head, self.profile_hash, ticket['findings_revision'],
                         ticket['assessment_id'], now, ticket['expires_at'], None, encoded(ticket)))
            self.store.append('feature_authorized', self.project_id, ticket, task_id=task_id, occurred_at=now, connection=con)
            return ticket

    def _check_ticket(self, con, ticket, head, task_id):
        ticket_id = ticket.get('id') if isinstance(ticket, dict) else ticket
        row = con.execute('SELECT * FROM qc_tickets WHERE ticket_id=? AND project_id=?', (ticket_id, self.project_id)).fetchone() if con is not None else None
        reasons = []
        if row is None:
            return {'valid': False, 'reasons': ['unknown_ticket']}
        stored = json.loads(row['payload'])
        if isinstance(ticket, dict) and ticket != stored:
            reasons.append('ticket_tampered')
        if row['consumed_at'] is not None:
            reasons.append('ticket_already_consumed')
        if row['head'] != head:
            reasons.append('canonical_head_changed')
        if row['task_id'] != task_id:
            reasons.append('task_changed')
        if row['profile_hash'] != self.profile_hash:
            reasons.append('profile_changed')
        if row['expires_at'] <= self.clock():
            reasons.append('ticket_expired')
        status = self._status(con, head)
        if row['findings_revision'] != status['findings_revision']:
            reasons.append('findings_changed')
        if not status['feature_allowed']:
            reasons.extend(status['reasons'])
        return {'valid': not reasons, 'reasons': list(dict.fromkeys(reasons)), 'ticket': stored}

    def revalidate_ticket(self, ticket, head, task_id):
        with self.store.reader() as con:
            return self._check_ticket(con, ticket, head, task_id)

    def consume_ticket(self, ticket, head, task_id):
        """Atomically revalidate/consume immediately before host promotion.

        Consumption is conservative across a crash. Recovery reconciles the
        host's exact promotion intent; a consumed ticket never authorizes a new
        attempt, even if the original promotion failed after consumption.
        """
        with self.store.transaction() as con:
            result = self._check_ticket(con, ticket, head, task_id)
            if not result['valid']:
                raise QualityGateError('Feature promotion blocked: ' + ', '.join(result['reasons']))
            con.execute('UPDATE qc_tickets SET consumed_at=? WHERE ticket_id=?', (self.clock(), result['ticket']['id']))
            self.store.append('feature_ticket_consumed', self.project_id, result['ticket'],
                              task_id=task_id, occurred_at=self.clock(), connection=con)
            return result

    # Explicit aliases for adapters using authorization terminology.
    authorize_feature = issue_feature_ticket
    recheck_feature = revalidate_ticket
    consume_feature = consume_ticket
