import copy,json,unittest

import project_backend as B


class WorkerBriefBoundTests(unittest.TestCase):
    def test_direction_keeps_the_project_aim_and_drops_controller_policy(self):
        brief={'project':'exampleapp','why':'Example tool','instruction':'Improve the shipped product.',
               'quality_profile':{'filler':'x'*9000},'action_policy':{'modules':{'a.py':'0'*64}},'worker_quality_policy':{'filler':'y'*6000}}
        config={'managed_brief':json.dumps(brief,indent=2),'engineering_policy':{'instruction':'Prefer suitable open-source reuse.'}}
        direction=B.worker_direction(config)
        self.assertLess(len(direction.encode()),1000)
        for kept in ('exampleapp','Example tool','Improve the shipped product.','Prefer suitable open-source reuse.'):self.assertIn(kept,direction)
        for dropped in ('quality_profile','action_policy','worker_quality_policy'):self.assertNotIn(dropped,direction)

    def test_direction_tolerates_text_and_non_object_briefs(self):
        self.assertTrue(B.worker_direction({'managed_brief':'Host direction '+'z'*5000}).startswith('Host direction'))
        self.assertLess(len(B.worker_direction({'managed_brief':'z'*5000})),2200)
        self.assertIn('["a"]',B.worker_direction({'managed_brief':'["a"]'}))
        self.assertIn('Follow the project roadmap.',B.worker_direction({}))

    def test_cloud_guidance_instruction_appears_once(self):
        steps=['Open both task files before editing. '+'s'*3000,'Add the end-to-end cross-tenant case. '+'t'*3000]
        saved={'summary':'Missing end-to-end proof','steps':steps,'guidance_id':'g1'};before=copy.deepcopy(saved)
        intervention={'guidance_id':'g1','instruction':'Apply the committed guidance:\n'+'\n'.join(steps),
                      'refs':[{'path':'/runtime/runs/r/candidate.patch','sha256':'a'*64}]*30}
        guidance=B.worker_guidance(saved,intervention)
        self.assertEqual(saved,before)
        text=json.dumps(guidance)
        self.assertLess(len(text),400)
        self.assertNotIn('sha256',text);self.assertNotIn('Open both task files',text)
        self.assertEqual(guidance['summary'],'Missing end-to-end proof')
        self.assertEqual(guidance['verified_recovery_intervention'],{})
        self.assertEqual(guidance['steps'],'Given in full as the first repair checklist item above.')
        self.assertEqual(B.worker_guidance({'steps':['']},{})['steps'],[''])

    def test_steps_stay_when_the_instruction_does_not_carry_them(self):
        saved={'steps':['Earlier advice the narrowing does not repeat']}
        intervention={'instruction':'Repair obligation 1 only','obligation':1,'anchor':{'path':'a.py','symbol':'f','quote':'def f():'},
                      'review':{'decision':'accept'},'refs':[],'coordinator_advice_id':None}
        guidance=B.worker_guidance(saved,intervention)
        self.assertEqual(guidance['steps'],saved['steps'])
        self.assertEqual(guidance['verified_recovery_intervention'],{'obligation':1,'anchor':intervention['anchor']})


if __name__=='__main__':unittest.main()
