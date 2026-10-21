"""A single synthetic OpenCode call; no GitHub credential or fleet mutation."""
import json
import os
from pathlib import Path
import threading
import subprocess
import tempfile
import autonomy

class Pulse:
    lost = threading.Event()

# Match the worker topology: the game clone is outside the Actions checkout.
# This caught a directory-selection bug that a checkout-local smoke missed.
root = Path(tempfile.mkdtemp(prefix='fleet-smoke-', dir=os.environ['RUNNER_TEMP']))
subprocess.run(['git', 'init', '-q', root], check=True)
autonomy.REPO = root
inputs = autonomy.worker_inputs()
(inputs/'assignment.json').write_text('{"check":"workspace-input","value":17}')
result = inputs/'plan.json'
result.unlink(missing_ok=True)
# Store the model log locally only. This command has no GitHub token and does
# not enter bootstrap(), control(), worker() or any private state operation.
autonomy.private_record = lambda *_: None
prompt = (f'Read {inputs / "assignment.json"}. Write {result} as exactly '
          '{"check":"workspace-input","value":17,"read_success":true}. '
          'This is a synthetic workspace I/O test. Change no other file and do not use GitHub. '
          'Do not change permissions or retry a denied tool call.')
autonomy.run_model(prompt, root, Pulse(), 2)
assert json.loads(result.read_text()) == {'check':'workspace-input','value':17,'read_success':True}
print('Synthetic workspace read/write succeeded; no fleet state or game code changed.')
