"""Frozen migration fixture from Midnight f583e1ffcc96bbaa086caca240565ef252eecfcb.

The exact historical public method generates old anchored rows in temporary
state. It is intentionally not updated with the current admission algorithm.
"""
import json
from pathlib import Path
from progress_recovery import RecoveryHeld,verify_refs

def offer_narrowing(self,identity,model,*,obligation, instruction,refs,review,guidance_id=None,anchor=None):
 if type(obligation) is not int or obligation<0 or not isinstance(instruction,str) or not instruction.strip() or len(instruction)>2000 or review.get('decision')!='accept' or review.get('issues'):raise RecoveryHeld('Narrowing needs one original obligation and separate independent acceptance')
 verify_refs(refs,self.roots)
 backed=False
 for item in refs:
  try:backed=backed or json.loads(Path(item['path']).read_text())==review
  except (ValueError,OSError):pass
 if not backed:raise RecoveryHeld('Independent narrowing review must match its saved result')
 evidence={'obligation':obligation,'instruction':instruction,'refs':refs,'review':review,'guidance_id':guidance_id,'anchor':anchor}
 with self.store.transaction() as con:
  row=self._scope(con,identity['id'])
  if not row:raise RecoveryHeld('Unknown recovery scope')
  if obligation>=len(json.loads(row['payload'])['obligations']):raise RecoveryHeld('Narrowing must reference an original acceptance obligation')
  current=self._guidance(con,identity)
  if guidance_id and (not current or current['guidance_id']!=guidance_id):raise RecoveryHeld('Guided work needs the actual committed Codex guidance')
  return self._offer(con,identity,'reviewed_narrowing',('guidance-'+guidance_id+'-' if guidance_id else '')+'obligation-'+str(obligation),model,evidence)

