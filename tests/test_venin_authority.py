"""No personal records or providers: synthetic authority and release fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import maintenance_authority as A
import coordinator_install as C
import deferred_release as R
import durability as D


class VeninAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.record=self.root/'midnight-authority.json'
        self.policy=self.root/'progress-policy.md';self.policy.write_text('Reviewed fixture policy\n')
        self.values={sheet:{cell:'unchanged fixture' for cell in cells} for sheet,cells in A.CELLS.items()}
        self.values['Automations'].update(A5='AUT-0004',K5='progress-based orchestrator recovery approved')
        self.values['Domains & Permissions']['A10']='Product Development'
        self.write_record()
        for target,name,value in ((A,'REGISTER',self.record),(A,'POLICY',self.policy),(C,'HERE',self.root),(C,'ROOT',self.root)):
            patch=mock.patch.object(target,name,value);patch.start();self.addCleanup(patch.stop)
        self.seal()

    def write_record(self, values=None, **extra):
        values=values if values is not None else self.values
        value={'schema_version':1,'automation':'AUT-0004','permissions':values,'permission_sha256':A.digest(values),**extra}
        self.record.write_text(json.dumps(value)+'\n');return value

    def seal(self):
        D.save_json(self.root/'runtime/resilience/authority.json',{'schema_version':2,'automation':'AUT-0004',
            'permission_sha256':A.digest(self.values),'scope':'Private checked Midnight maintenance only; product permissions unchanged'})

    def proposal(self, **extra):
        value={'schema_version':2,'kind':'venin-record-migration','before':copy.deepcopy(self.values),
            'after':copy.deepcopy(self.values),'authority_record':str(self.record),
            'record_sha256':hashlib.sha256(self.record.read_bytes()).hexdigest(),'policy_path':str(self.policy),**extra}
        path=self.root/'authority-proposal.json';path.write_text(json.dumps(value))
        review={'authority_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'policy_sha256':hashlib.sha256(self.policy.read_bytes()).hexdigest()}
        (self.root/'deployment-review.json').write_text(json.dumps(review))
        (self.root/'authority-record-update.json').write_text(json.dumps({'permission_before':A.digest(value['before']),
            'permission_after':A.digest(value['after']),'policy_after':review['policy_sha256']}))
        return review

    def test_default_uses_only_current_venin_record_and_preserves_old_seal(self):
        self.seal();before=(self.root/'runtime/resilience/authority.json').read_bytes()
        with mock.patch.object(A,'legacy_permissions',side_effect=AssertionError('No personal fallback')):
            self.assertEqual(A.permissions(),self.values)
            self.assertEqual(A.check(self.root)['permission_sha256'],A.digest(self.values))
            self.record.unlink()
            with self.assertRaises(OSError):A.check(self.root)
        self.assertEqual((self.root/'runtime/resilience/authority.json').read_bytes(),before)

    def test_fresh_drift_is_held_without_resealing_or_using_checkpoint_bytes(self):
        self.seal();path=self.root/'runtime/resilience/authority.json';before=path.read_bytes()
        changed=copy.deepcopy(self.values);changed['Automations']['K5']+=' changed'
        self.write_record(changed)
        with self.assertRaisesRegex(ValueError,'permission text changed'):A.check(self.root)
        with self.assertRaisesRegex(ValueError,'exact reviewed'):A.seal(self.root)
        with self.assertRaisesRegex(ValueError,'exact reviewed'):A.seal(self.root,expected_permission_sha256=A.digest(self.values))
        self.assertEqual(path.read_bytes(),before)

    def test_exact_migration_can_refresh_legacy_seal_without_permission_change(self):
        self.seal();receipt=A.seal(self.root,expected_permission_sha256=A.digest(self.values))
        self.assertEqual(receipt['schema_version'],2);self.assertEqual(receipt['permission_sha256'],A.digest(self.values))
        self.assertEqual(A.check(self.root),receipt)

    def test_malformed_duplicate_nonfinite_or_incomplete_record_is_rejected(self):
        good=self.record.read_bytes();variants=[b'null',b'[]',b'{}',b'{"schema_version":1,"schema_version":1}',
            good.replace(b'"schema_version": 1',b'"schema_version": true'),
            good.replace(b'"AUT-0004"',b'"wrong"',1),good.replace(b'"schema_version": 1',b'"schema_version": NaN'),
            good.replace(A.digest(self.values).encode(),b'0'*64)]
        for raw in variants:
            with self.subTest(raw=raw[:60]):
                self.record.write_bytes(raw)
                with self.assertRaises((ValueError,TypeError)):A.permissions()
                self.assertEqual(self.record.read_bytes(),raw)

    def test_links_fifo_oversized_and_replaced_record_are_held(self):
        original=self.record.read_bytes();other=self.root/'other.json';other.write_bytes(original)
        self.record.unlink();self.record.symlink_to(other)
        with self.assertRaises((ValueError,OSError)):A.permissions()
        self.record.unlink();os.link(other,self.record)
        with self.assertRaises(ValueError):A.permissions()
        self.record.unlink();os.mkfifo(self.record)
        with self.assertRaises(ValueError):A.permissions()
        self.record.unlink();self.record.write_bytes(b'x'*(128*1024+1))
        with self.assertRaises(ValueError):A.permissions()
        self.record.write_bytes(original);read=os.read
        def replace(fd,size):
            raw=read(fd,size);replacement=self.root/'replacement';replacement.write_bytes(original);replacement.replace(self.record);return raw
        with mock.patch.object(A.os,'read',side_effect=replace):
            with self.assertRaisesRegex(ValueError,'changed during'):A.permissions()

    def test_installer_binds_unchanged_permissions_current_record_and_policy(self):
        self.seal();review=self.proposal();bindings=C.approved_authority(review)
        self.assertEqual(bindings['permission_digest'],A.digest(self.values))
        self.assertEqual(bindings['authority_record_sha256'],hashlib.sha256(self.record.read_bytes()).hexdigest())
        self.assertTrue(C.authority_current(bindings))
        self.write_record(provenance='metadata changed during wait')
        with self.assertRaisesRegex(RuntimeError,'exact reviewed'):C.authority_current(bindings)

    def test_migration_cannot_change_permission_or_redirect_record_or_policy(self):
        for mode in ('permission','record','policy','version'):
            with self.subTest(mode=mode):
                extra={}
                if mode=='permission':
                    extra['after']=copy.deepcopy(self.values);extra['after']['Automations']['K5']+=' widened'
                if mode=='record':extra['authority_record']=str(self.root/'elsewhere')
                if mode=='policy':extra['policy_path']=str(self.root/'elsewhere')
                if mode=='version':extra['schema_version']=True
                with self.assertRaisesRegex(RuntimeError,'exact reviewed'):C.approved_authority(self.proposal(**extra))

    def test_policy_drift_and_missing_migration_receipt_block_installer(self):
        review=self.proposal();self.policy.write_text('Changed policy')
        with self.assertRaisesRegex(RuntimeError,'independent approval'):C.approved_authority(review)
        review=self.proposal();(self.root/'authority-record-update.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'migration receipt'):C.approved_authority(review)


    def test_migration_cannot_introduce_new_permissions_with_matching_before_and_after(self):
        changed=copy.deepcopy(self.values);changed['Automations']['K5']+=' expanded permission'
        self.write_record(changed)
        review=self.proposal(before=changed,after=changed)
        with self.assertRaises((ValueError,RuntimeError)):C.approved_authority(review)

    def test_final_receipt_read_cannot_hide_changes_to_any_bound_evidence(self):
        for name in ('record','policy','proposal','review','receipt','seal'):
            with self.subTest(name=name):
                self.write_record();self.policy.write_text('Reviewed fixture policy\n');self.seal()
                review=self.proposal();bindings=C.approved_authority(review)
                paths={'record':self.record,'policy':self.policy,'proposal':self.root/'authority-proposal.json',
                    'review':self.root/'deployment-review.json','receipt':self.root/'authority-record-update.json',
                    'seal':self.root/'runtime/resilience/authority.json'}
                target=paths[name];fired=False;changed=None
                original_read=D.read_json;original_fresh=A.fresh_bytes
                def mutate(path,result):
                    nonlocal fired,changed
                    if Path(path)==paths['receipt'] and not fired:
                        fired=True
                        if name=='policy':changed=b'Changed during final receipt read\n'
                        else:
                            value=json.loads(target.read_bytes());value['concurrent_change']='owner change'
                            changed=json.dumps(value).encode()
                        target.write_bytes(changed)
                    return result
                def read(path,*args,**kwargs):return mutate(path,original_read(path,*args,**kwargs))
                def fresh(path,*args,**kwargs):return mutate(path,original_fresh(path,*args,**kwargs))
                with mock.patch.object(D,'read_json',side_effect=read),mock.patch.object(A,'fresh_bytes',side_effect=fresh):
                    with self.assertRaises((RuntimeError,ValueError)):C.authority_current(bindings)
                self.assertTrue(fired);self.assertEqual(target.read_bytes(),changed)
                # Fixture-only restoration keeps each subcase independent.
                if name=='seal':self.seal()

    def test_reviewed_proposal_cannot_select_a_conflicting_checkpoint_record(self):
        review=self.proposal();path=self.root/'authority-proposal.json';reviewed=path.read_bytes()
        changed=json.loads(reviewed);self.write_record(provenance='unreviewed replacement')
        changed['record_sha256']=hashlib.sha256(self.record.read_bytes()).hexdigest()
        D.save_json(path,changed);path.write_bytes(reviewed);checkpoint=D.snapshot(path)
        with self.assertRaises((RuntimeError,ValueError)):C.approved_authority(review)
        self.assertEqual(path.read_bytes(),reviewed);self.assertEqual(D.snapshot(path),checkpoint)

    def test_review_and_receipt_checkpoint_conflicts_are_held_and_preserved(self):
        for name in ('deployment-review.json','authority-record-update.json'):
            with self.subTest(name=name):
                review=self.proposal();path=self.root/name;original=path.read_bytes()
                changed=json.loads(original);changed['unreviewed']='different checkpoint'
                D.save_json(path,changed);path.write_bytes(original);checkpoint=D.snapshot(path)
                with self.assertRaises((RuntimeError,ValueError)):C.approved_authority(review)
                self.assertEqual(path.read_bytes(),original);self.assertEqual(D.snapshot(path),checkpoint)
                D.save_bytes(path,original)

    def test_installer_rejects_duplicate_or_nonfinite_review_proposal_and_receipt(self):
        for name in ('deployment-review.json','authority-proposal.json','authority-record-update.json'):
            for extra in (b',"ambiguous":1,"ambiguous":2}',b',"nonfinite":NaN}'):
                with self.subTest(name=name,extra=extra):
                    review=self.proposal();path=self.root/name;path.write_bytes(path.read_bytes().rstrip()[:-1]+extra)
                    if name=='authority-proposal.json':
                        review['authority_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                        (self.root/'deployment-review.json').write_text(json.dumps(review))
                    with self.assertRaises((RuntimeError,ValueError)):C.approved_authority(review)

    def test_valid_durable_evidence_and_restricted_legacy_upgrade_remain_usable(self):
        before=copy.deepcopy(self.values)
        before['Automations']['K5']='prior limited orchestrator permission'
        before['Domains & Permissions']['G10']='prior limited developer permission'
        review=self.proposal(kind='restricted-policy-upgrade',before=before)
        for name in ('deployment-review.json','authority-proposal.json','authority-record-update.json'):
            path=self.root/name;D.save_bytes(path,path.read_bytes())
        bindings=C.approved_authority(review)
        self.assertEqual(bindings['permission_digest'],A.digest(self.values))
        self.assertTrue(C.authority_current(bindings))

    def test_receipt_or_review_metadata_changes_between_guards_need_new_binding(self):
        for name in ('deployment-review.json','authority-record-update.json'):
            with self.subTest(name=name):
                review=self.proposal();bindings=C.approved_authority(review);path=self.root/name
                value=json.loads(path.read_bytes());value['later_review']='new evidence'
                path.write_text(json.dumps(value));changed=path.read_bytes()
                with self.assertRaisesRegex(RuntimeError,'authority changed'):C.authority_current(bindings)
                self.assertEqual(path.read_bytes(),changed)

    def test_invalid_latest_checkpoint_never_falls_back_to_older_approval(self):
        for mode in ('oversized','corrupt','deleted'):
            with self.subTest(mode=mode):
                review=self.proposal();path=self.root/'authority-proposal.json';original=path.read_bytes()
                D.save_bytes(path,original)
                if mode=='oversized':D.commit_snapshot(path,b'x'*(128*1024+1))
                elif mode=='deleted':D.commit_snapshot(path,b'',deleted=True)
                else:
                    D.commit_snapshot(path,original)
                    with D.connect(path,writable=True) as con:
                        con.execute('UPDATE snapshots SET digest=? WHERE name=? AND version=(SELECT MAX(version) FROM snapshots WHERE name=?)',
                            ('0'*64,path.name,path.name));con.commit()
                checkpoint_bytes=D.database(path).read_bytes()
                with self.assertRaisesRegex(RuntimeError,'checkpoint'):C.approved_authority(review)
                self.assertEqual(path.read_bytes(),original);self.assertEqual(D.database(path).read_bytes(),checkpoint_bytes)
                D.save_bytes(path,original)

    def test_new_checkpoint_after_earlier_evidence_read_holds_even_for_same_bytes(self):
        review=self.proposal();bindings=C.approved_authority(review);proposal=self.root/'authority-proposal.json'
        original=A.fresh_bytes;fired=False
        def create_checkpoint(path,*args,**kwargs):
            nonlocal fired
            result=original(path,*args,**kwargs)
            if Path(path).name=='authority-record-update.json' and not fired:
                fired=True;D.commit_snapshot(proposal,proposal.read_bytes())
            return result
        with mock.patch.object(A,'fresh_bytes',side_effect=create_checkpoint):
            with self.assertRaisesRegex(RuntimeError,'changed during'):C.authority_current(bindings)
        self.assertTrue(fired);self.assertEqual(D.snapshot(proposal)['payload'],proposal.read_bytes())

    def test_frozen_module_uses_current_record_not_a_bundled_copy(self):
        import importlib.util
        frozen=self.root/'frozen';frozen.mkdir()
        module_path=frozen/'maintenance_authority.py';module_path.write_bytes(Path(A.__file__).read_bytes())
        (frozen/'midnight-authority.json').write_bytes(self.record.read_bytes())
        spec=importlib.util.spec_from_file_location('frozen_authority_fixture',module_path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(str(module.REGISTER),str(Path.home()/'Developer'/'midnight-records'/'midnight-authority.json'))
        with mock.patch.object(module,'REGISTER',self.record):
            self.assertEqual(module.check(self.root)['permission_sha256'],A.digest(self.values))
            changed=copy.deepcopy(self.values);changed['Automations']['K5']+=' current owner change'
            self.write_record(changed)
            with self.assertRaisesRegex(ValueError,'permission text changed'):module.check(self.root)

    def test_legacy_workbook_is_explicit_only_and_retains_exact_cell_values(self):
        import zipfile
        from xml.sax.saxutils import escape
        book=self.root/'legacy.xlsx';ns=A.NS['m'];rel=A.NS['r']
        with zipfile.ZipFile(book,'w') as z:
            z.writestr('xl/sharedStrings.xml','<sst xmlns="'+ns+'"/>')
            z.writestr('xl/workbook.xml','<workbook xmlns="'+ns+'" xmlns:r="'+rel+'"><sheets>'+
                ''.join('<sheet name="'+escape(sheet)+'" r:id="r'+str(i)+'"/>' for i,sheet in enumerate(self.values))+'</sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships>'+
                ''.join('<Relationship Id="r'+str(i)+'" Target="worksheets/s'+str(i)+'.xml"/>' for i in range(2))+'</Relationships>')
            for i,(sheet,cells) in enumerate(self.values.items()):
                z.writestr('xl/worksheets/s'+str(i)+'.xml','<worksheet xmlns="'+ns+'"><sheetData><row>'+
                    ''.join('<c r="'+cell+'" t="inlineStr"><is><t>'+escape(value)+'</t></is></c>' for cell,value in cells.items())+
                    '</row></sheetData></worksheet>')
        self.assertEqual(A.permissions(book),self.values)
        self.record.unlink()
        with mock.patch.object(A,'legacy_permissions',side_effect=AssertionError('No fallback')):
            with self.assertRaises(OSError):A.permissions()



class PersonalPublicationRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.value={'id':'release','completion_record':{'taskId':'task.midnight-temperature-fans-20261101','expectedVersion':4}}

    def test_no_personal_send_and_original_uncertain_outbox_is_preserved(self):
        originals={}
        for name,value in [('operating-picture-request.json',{'operationId':'original-uncertain'}),
                ('operating-picture-result.json',{'status':'uncertain'}),('sensor-health.json',{'passed':True})]:
            path=self.root/name;path.write_text(json.dumps(value));originals[name]=path.read_bytes()
        with mock.patch.object(R.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout='{"status":"recorded"}')) as send:
            first=R.completion_record(self.value,self.root);second=R.completion_record(self.value,self.root)
        send.assert_not_called();self.assertEqual(first,second)
        self.assertEqual(first['status'],'retired_personal_reporting')
        self.assertEqual(first['release_id'],'release')
        for name,raw in originals.items():self.assertEqual((self.root/name).read_bytes(),raw)

    def test_unconfigured_record_needs_no_personal_or_local_completion_write(self):
        with mock.patch.object(R.subprocess,'run',side_effect=AssertionError('No process')):
            self.assertEqual(R.completion_record({'id':'release'},self.root),{'status':'not_configured'})
        self.assertEqual(list(self.root.iterdir()),[])

    def test_uncertain_local_record_replays_and_conflicts_do_not_send_or_overwrite(self):
        save=R.D.save_json
        def lost_reply(path,value):save(path,value);raise RuntimeError('Lost local acknowledgement')
        with mock.patch.object(R.subprocess,'run',side_effect=AssertionError('No personal process')):
            with mock.patch.object(R.D,'save_json',side_effect=lost_reply):
                with self.assertRaisesRegex(RuntimeError,'Lost local'):R.completion_record(self.value,self.root)
            receipt=R.completion_record(self.value,self.root)
            self.assertEqual(receipt['status'],'retired_personal_reporting')
            path=self.root/'venin-completion-record.json';conflict={**receipt,'release_id':'other'};save(path,conflict)
            before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'conflicts'):R.completion_record(self.value,self.root)
            self.assertEqual(path.read_bytes(),before)

    def test_completed_bootstrap_needs_no_new_job_or_personal_publication(self):
        import test_deferred_bootstrap as F
        import deferred_bootstrap as B
        fixture=F.FrozenBootstrapTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        value,args,store,command,_=fixture.dispatch_fixture()
        value['completion_record']=self.value['completion_record']
        directory=Path(args.root)/'runtime/deferred-releases'/value['id'];directory.mkdir(parents=True)
        D.save_json(directory/'state.json',{'state':'complete','installed':value['commit']})
        with mock.patch.object(R.subprocess,'run',side_effect=AssertionError('No personal process')):
            self.assertEqual(B.dispatch(value,args),0);self.assertEqual(B.dispatch(value,args),0)
        store.submit.assert_not_called();command.assert_not_called()
        self.assertEqual(D.read_json(directory/'state.json')['state'],'complete')


if __name__=='__main__':unittest.main()
