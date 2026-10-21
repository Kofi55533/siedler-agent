"""Read-only real worker setup/planner smoke; no lease, push, PR or state write."""
import json
import os
import threading
import time
import autonomy as a

class Pulse:
    lost=threading.Event()

state=a.fs.Store().read()[0][a.KEY]
u=state['units']['game-logic-core-2'].copy()
u.update(wip=None,branch='agent/local-smoke-never-pushed')
a.setup_worker_repo(u)
a.setup_research()
scratch=a.worker_inputs()/'real-plan-smoke';scratch.mkdir(exist_ok=True)
u['native_source_input']=os.environ['S5_NATIVE_SOURCE']
(scratch/'assignment.json').write_text(json.dumps(u,indent=2))
a.private_record=lambda *_:None
prompt=a.planning_prompt(scratch,[],[],'',time.monotonic()+15*60)
try:a.run_model(prompt,a.REPO,Pulse(),12)
except a.ToolAccessRefusal:
    # The refused call stays refused. Only a valid permitted-work plan counts.
    if not (scratch/'plan.json').exists():raise
plan=a.core.validate_plan(json.loads((scratch/'plan.json').read_text()),u)
assert not a.git('status','--porcelain'), 'Read-only planning changed game source'
print('Real worker setup and native-input plan validated; no state, source, push or PR mutation.')
print('Planned obligations:',len(plan['addresses']),'concrete resources:',len(plan['resources']))
