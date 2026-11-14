# Production worker qualification

M10 supplements the existing fixed label/range probes with `manifest_repair`
and `summary_creation`. The original two source/task/oracle values are unchanged;
their oracle hashes are also checked by the receipt verifier. The additional
fixtures are fixed host code in `worker_probe_cases.py`, protected from automatic
maintenance, with no product data, network, accounts or dependencies.

The manifest case repairs concrete validation behavior in an existing padded
module. The summary case creates an absent module. Both require five successful
named file reads before the first edit, including the absent-file observation
for creation. Their briefs exceed 44,000 bytes and at least one actual action
request must record 14,000 prompt tokens. These size gates exercise accumulated
production-sized context; they do not establish semantic coverage of every
backlog task. Each case must make a meaningful change that passes its immutable
semantic oracle, preserves protected inputs and reaches verified finish after
a successful syntax check following the final edit.

Each representative brief contains exactly one unchecked task for the frozen
Ralphy markdown parser. An offline integration runs the actual generated brief
through that wrapper with a fake client; plain task prose alone does not dispatch.
M25 / #84 records this fixture boundary. Task wording and retained context are
unchanged, and both representative oracles remain protected.

The exact production Ralphy entry invokes a confinement proxy and then the
candidate `local_worker.py` / `structured_worker.py`. The existing host broker
executes only the fixed syntax command. The independent host executes the fixed
semantic oracle after the candidate process group drains. The proxy, sandbox
and disposable data distinguish qualification from a product run; the result
continues to report `native_production_client: false` and separately identifies
the production worker entry exercised. No qualification grants coding admission
or product acceptance.

Version-two qualification binds all four cases, exact worker sources, execution
dependencies, qualification sources, model descriptor, inference policy and
actual inference/tool receipts. Truncation, changed sources/policy/model/oracles,
insufficient context or reads, missing checks and partial finish all fail.
The host waits for the final tool result and exactly one ordered sequence of
`turn.completed`, `worker.result_handoff` with integer schema version 1 and
`status: completed`, then the wrapper's actual zero client exit. Failed, missing,
malformed, duplicate or out-of-order handoff records fail even without a separate
error line. Client schema versions must also be integers, not booleans. M24 / #83
records this integration check following the safe result adapter in M21/M22.
A ready marker alone is insufficient. A
recoverable edit precondition error before inference needs no invented edit
receipt; any existing edit inference receipt remains fully validated. These
publication boundaries are tracked by M20 / #75.
Legacy version-one records can be inspected explicitly with
`worker_recovery.qualification(..., allow_legacy=True)`; that returns
`representative: false`. Ordinary qualification and recovery certificates require
the representative version. Legacy records and spent charges are never rewritten.

Run deterministic and confinement checks first:

```sh
python3 -m unittest discover -s tests -p test_worker_qualification_receipts.py -v
python3 -m unittest discover -s tests -p test_recovery_worker_probe.py -v
python3 -m unittest discover -s native_tests -p '*probe_confinement.py' -v
python3 -m unittest discover -s tests -v
```

The existing trusted `core_repair.qualify_worker` entry runs the native broker
with current controls and resource ownership. For an operator qualification,
first record an explicit bounded qualification window, exact reviewed source
and pinned descriptor; acquire the existing local and heavy-memory lanes, and
honor current pauses, install controls, ownership and deadline throughout the
call. The actual worker acquires the existing GPU lock. Then invoke:

```sh
python3 -I -B worker_probe.py --source /absolute/reviewed/source \
  --output /absolute/new/disposable/output --model-file /absolute/pinned-model.json
```

Do not run this command without that enclosing host control/resource procedure,
reuse an existing output directory, silently extend a failed window or replace
the pinned model. Each worker case retains its maximum 240-second execution
limit; all four share a 580-second internal deadline inside the existing
600-second host phase. Output remains private. Preserve incomplete results and
failures; never turn a partial proof into a successful receipt.

Exact-source native success, reviewed adoption and real independently accepted
repair/feature work are separate gates. M14 verifies the final installed release;
M15 and M16 establish productive work and the full unattended scheduled night.
