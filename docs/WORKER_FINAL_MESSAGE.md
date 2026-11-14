# Worker final-message ownership

Midnight's host allocates a fresh `EXAMPLEAPP_AGENT_RESULT` in each private run
(or the existing `EXAMPLEAPP_AGENT_EVENTS` / `EXAMPLEAPP_AGENT_LOG` sidecar
convention supplies `EVENTS.last`). The adapter passes that path directly as
`--output-last-message`. The typed client atomically publishes there before
emitting `turn.completed`; the adapter records its actual exit separately.

The vendored Ralphy loop uses plain `mktemp` for stdout. On this Mac that path
is outside the fixed worker sandbox even when `TMPDIR` names the private case.
Deriving the client destination from stdout therefore failed after a successful
edit, syntax check and finish tool call. The retained M10 native-v2 run and
M21 issue #79 record this failure; no qualification was accepted.

Ralphy also deletes `CODEX_LAST_MESSAGE_FILE` during cleanup. Midnight keeps
that parser file temporary and distinct: after a successful client exit the
adapter makes a validated regular-file snapshot of the durable host result for
the unchanged parser. The inline host helper is covered by the existing adapter
source hash. Before launching the client it captures resolved parent paths and
device/inode identities; after exit it traverses canonical ancestors through
no-follow directory descriptors and rechecks those identities. It opens the
result without following links or blocking on special files, requires a single
link regular file containing 1–65,536 bytes of UTF-8, and checks metadata again
after reading. It creates the separate parser snapshot exclusively. A source or
parser failure emits a durable error and `worker.result_handoff: failed`; the
actual client exit remains separately recorded and unchanged. Successful
publication records `worker.result_handoff: completed`. Independent M21 review
reproduced the unsafe-copy and swallowed-error defects using fake data; M22 #80
tracks those corrections.

With neither a result nor events destination, the legacy temporary client
destination remains and is removed after its safe parser snapshot is published. This unconstrained fallback
is not the qualification entry path.

The parent shell completes preflight before giving upstream cleanup a parser
filename. On refusal the cleanup variable stays empty, so a retained parser
collision cannot be deleted. A pre-existing result destination (including a dangling symlink) is retained
and the adapter refuses to launch the client. Its durable error requests
reconciliation of the original run; it does not emit a fictitious client exit
or overwrite an earlier result. Hosts must allocate a new per-run destination
when a genuinely new launch is admitted. Outer-loop exit zero still does not
prove worker completion. Submission, final tool result, `turn.completed`,
actual client exit and the original acceptance gates all remain required.

This small adapter retains the licensed vendored implementation and its exact
`PROVENANCE.json` hashes. It changes no sandbox permissions, worker scope,
model, inference budget or protected qualification oracle.

Verification uses `python3 ralphy/test_adapter.py`, the real sandbox regression
`python3 -m unittest discover -s native_tests -p test_probe_final_message.py -v`,
existing confinement tests, and the complete controller suite. The focused `tests/test_worker_result_handoff.py` suite also exercises unsafe
files, output limits, stale/parser collision and destination replacement. Those offline
checks use fixed fake clients and temporary repositories, never a model.
The exact integrated production worker must then pass all four native M10
cases before rollout. These source checks alone do not establish qualification,
installation, product acceptance or unattended operation.

## Production completion and restart (M23 / #81)

Managed typed workers now keep running after the ready marker so that the final
tool receipt, turn completion, safe result handoff and actual client result can
be published. Pauses, absolute cutoff, timeout and owned-process drainage retain
their original priority. Production uses the same terminal verifier as native
qualification and also requires a successful completed process receipt. A
wrapper exit or saved `finished` flag cannot establish typed completion.

Pending review and microstep recovery revalidate the original model, candidate,
manifest, syntax and complete publication evidence. New local promotion and
remote delivery effects repeat that gate. GitHub delivery may first reconcile
an already completed remote merge using its existing exact-head receipts; an
uncertain PR creation remains retained when the new-effect gate holds. Existing
accepted commits still recover as historical facts without inventing newer
worker evidence. Original candidates, reservations and charges stay intact.

Explicit cloud and standalone compatibility routes retain their existing
handoff contracts. Host verification of a retained candidate remains a separate
recorded route with complete semantic checks and independent review; it never
creates a typed-worker completion receipt. Unfinished microstep verification
continues to record `worker_finished: false`, zero new coding launches and no
product acceptance. Malformed or unsafe typed metadata stays held.

A failed host result handoff is reported even when both client and wrapper exit
zero. Its phase is `result_handoff`; no inference budget or usage is invented.
The new production regressions use real local edit/syntax/finish APIs, temporary
repositories, controlled subprocesses and fake providers. Run them with
`python3 -m unittest discover -s tests -p test_worker_terminal_handoff.py -v`,
then run the complete suite. These source changes do not install the pipeline or
establish accepted product work or unattended-night commissioning.

M26 / #85 keeps bounded metadata access and process/model/completion decoding
inside those hold boundaries. Invalid JSON, invalid UTF-8, wrong shapes,
unsafe/missing files and metadata-access errors preserve their original bytes
and candidate/charge history. They cannot turn into either worker completion or
a generic scheduler error before recovery can report the hold.

## Productive failure-boundary matrix (M11 / #57)

`tests/test_productive_failure_boundaries.py` retains inspectable before/after
observations for nine disposable fault rehearsals: proof transaction rollback;
spent proof recovery after commit; truncated inference with a retained edit;
failed host checks; independent reviewer rejection; uncertain PR creation and
remote merge; a local commit before acceptance receipt publication; failed or
retained work in session reporting; and a committed session ledger before its
checkpoint. Replaying a delivery may refresh `observed_at`; its complete records
are retained while every acceptance identity and effect count must stay stable.

The cases use real durable stores, temporary Git repositories, controlled local
syntax/semantic checks and fake provider APIs. They preserve candidates and
charges, reject false completion, and check recovery twice without a second
provider request, merge, accepted receipt or session achievement. The local
commit-recovery case isolates finding/procedure housekeeping; existing profile
and manager-recovery tests retain that separate coverage.

Run `python3 -m unittest discover -s tests -p test_productive_failure_boundaries.py -v`,
the existing action/delivery/recovery integration groups, and the complete suite.
A source-bound operator rehearsal exports each method's `evidence` before its
temporary fixture is cleaned up, retaining complete synthetic state/receipt rows
and candidate hashes. This is deterministic failure evidence, separate from
native worker qualification, deployment and real accepted-work/night commissioning.
