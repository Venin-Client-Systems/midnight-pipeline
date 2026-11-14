# Bounded worker inference policy

Tracking: M03 #49,
implementation M04 #50,
parent #46.

## Measurement and conclusion

The two retained production failures were replayed on 13 November 2026 using
the unchanged `structured_worker.infer()` implementation at `b5e1bec`, the
original host brief, all four saved read results, the production action schema
and the exact pinned model. The action-selection failure is reproducible: both
default requests exhausted 1,024 generated tokens while emitting thinking text
and **zero final action text**. Adding only `think: false` returned valid typed
actions well inside that same cap for both contexts.

| Retained task / policy | Prompt tokens | Generated tokens | Thinking bytes | Final-text bytes | Wall seconds | Result |
|---|---:|---:|---:|---:|---:|---|
| Export repair / production default | 16460 | 1024 | 4146 | 0 | 129.742 | `length`, rejected |
| Export repair / thinking disabled | 16462 | 33 | 0 | 140 | 2.332 | Valid `read_file` |
| Tenant repair / production default | 15475 | 1024 | 4067 | 0 | 109.139 | `length`, rejected |
| Tenant repair / thinking disabled | 15477 | 39 | 0 | 167 | 2.564 | Valid `read_file` |

The original retained prompt counts were also 16460 and 15475. The replayed
message payload for each default/candidate pair is byte-identical; the two-token
prompt difference appears after the thinking option changes the model's request
handling. All other request fields, including the schema, model, context and
output cap, are identical. No temperature or other model parameter was changed.
The live metadata advertises `thinking` capability.

These measurements confirm exhaustion while producing thinking output for the
two observed requests. They do not provide a token count separately attributable
to each output channel; recorded byte counts must not be described as token
counts. The timings include differing prefill/cache conditions and do not prove
a general speedup factor or model superiority. Two prompts do not establish
production reliability across the backlog.

Model: `local-qwen38:context32`, context 32768, digest
`bbdf6bef4023875f266e34ab4fa6ce07450952222d2ccef8fe8f4864139dc031`,
parameters digest
`d6453009a7b083f19992832476263cbad3bc8c1c70663d75e758297122de5db5`.
The model was not replaced or reconfigured.

## Selected phase policy and remaining qualification gate

The non-thinking edit follow-through completed both JSON responses, but did not
demonstrate a functional repair:

| Edit frame | Generated tokens | Wall seconds | In-memory result |
|---|---:|---:|---|
| Export repair | 38 | 29.249 | Unique, syntax-valid replacement changing only a comment |
| Tenant repair | 59 | 25.973 | Identical old/new text with curly quotes; no source match |

M18 #66 was created
and linked before implementation. The subsequent comparison used the identical
edit frames and original 4096-token cap, changing only the thinking option:

| Edit frame / production default | Generated tokens | Thinking bytes | Final-text bytes | Wall seconds | Result |
|---|---:|---:|---:|---:|---|
| Export repair | 4096 | 4840 | 143 | 174.094 | `length`, rejected |
| Tenant repair | 1281 | 5028 | 263 | 91.396 | Same nonmatching no-op |

Thinking did not rescue either proposal and introduced another truncation. The
selected bounded policy disables advertised thinking for action selection,
exact-text edit generation and worker chat. Action selection has direct paired
completion evidence; edit selection avoids the observed truncation without
claiming semantic improvement. Chat uses the same explicit structured-output
policy as a conservative implementation choice, not a measured chat-quality
finding. M18 remains open until meaningful functional edits pass M04/M10 and real
acceptance passes M15. This is a policy selected for implementation and
qualification, not a claim that productive operation is already qualified.

Inspection of the full task contracts and source shows why fresh diagnosis is
needed. The export source already uses `output_file is not None`, exclusive
creation and separate `OSError`/`ValueError` handling. Its tests still call the
real lab instead of the requested socket-free fixture and omit the prescribed
deterministic race and several failure cases. The tenant source already rejects
same-tenant pairs and permits equal user IDs across tenants. Its test file has
only four combinations, weak not-inconclusive assertions for the different-tenant
cases, and lacks the required denial/request-trace and malformed-identity cases.
These are unfulfilled original test obligations, not evidence that the entire
task is already accepted. No host checks or independent product review were
waived or newly credited.

The following requirements constrain the implementation:

1. Apply an explicit policy to the managed production worker session. Determine
   thinking support from bounded local model metadata once per session, bind it
   to the exact pinned descriptor, and disable thinking for every structured phase
   when advertised. Keep
   non-thinking models and the separate native-provider policy compatible.
2. Preserve the phase budgets: action selection 1024, exact-text edit generation
   4096 and chat 1536 tokens. Preserve the 90000-byte inference input limit,
   bounded history, exact writable scope and absolute worker deadline. There is
   no evidence here supporting a larger cap or a model replacement.
3. Metadata lookup is bounded by the remaining deadline. If a managed session
   cannot determine the applicable policy, retain a diagnostic failure rather
   than silently returning to unqualified thinking behaviour. Cache only inside
   that exact worker session; another model/session must resolve its own policy.
4. Give the policy a stable version/identity. Receipts and later qualification
   bind worker source, policy and model. A new run ID or timestamp is provenance,
   not a new intervention. M05 carries requested budgets and measured usage
   through the full stack; M06 enforces admission independently of guidance.
5. Continue rejecting truncated, malformed, wrong-model, context-saturated or
   late output before applying an action or edit. A full action/edit response
   still requires host schema, file-scope, fresh-read, unique-match and syntax
   validation. Only a changed file plus a successful check after the final edit
   may produce the verified finish handoff.

The low-level inference helper may keep its existing direct-call interface for
compatibility; the production worker entry must always install the managed
session policy. Tests must exercise that entry, not only a helper invoked with
an optional flag. The independent local supervisor remains a separate mechanism.

## Evidence and exact procedure

Private artifacts are under `runtime/productive-operation-20261113/` in the
canonical Developer checkout. The six-minute `m03-replay/window.json` binds the
diagnostic to its model, source hashes, absolute deadline and no-product-write
scope. The two recorded run IDs are:

- `20261113T091348Z-G202610312151445b34-976079`
- `20261113T091830Z-G27722fa9312e7f97edf576a2-1af68a`

For each run, `m03_replay.py` reconstructs `brief_messages()`, then appends the
four successful saved action/tool pairs exactly as `main()` does, and applies
the unchanged history bound. It invokes the production inference function with
the action schema and 1024-token cap twice. The second request changes only
`think` to false. It records exact messages/request JSON, private raw streams,
completion reason, usage, elapsed time and reasoning/final bytes. It applies no
action. The script acquires the shared local phase, heavy-memory and GPU locks,
honours pause/install controls, and creates no coding proof or schedule override.

```sh
python3 runtime/productive-operation-20261113/m03_replay.py
```

This command deliberately refuses to reuse an existing evidence directory.
Preserve the original invocation and receipts; any further experiment needs a
new explicit evidence directory and deadline. Do not blindly rerun an uncertain
call. The independently bounded edit follow-through uses:

```sh
python3 runtime/productive-operation-20261113/m03_edit_replay.py
```

That procedure follows additional read decisions from the saved non-thinking
action, constructs the production `edit_request_messages()` frame and requests
one replacement under the unchanged 4096 cap. It checks exact matching and Python
syntax only in memory. It never executes generated code or applies model edits. It follows only bounded
read decisions and writes no product files. Its separate three-minute window, streams and memory-check receipts are
retained in `m03-edit-replay/`. Its read emulator allows larger files and represents
absent files differently from production; all seven observed additional reads
were existing 623–4565-byte files. Independent reconstruction through the real
`Worker.action(read_file)` and `edit_snapshot()` produced identical frames.
Future qualification must use the production methods, including absent files.

```sh
python3 runtime/productive-operation-20261113/m18_compare_edits.py
```

This separate ten-minute comparison reuses the saved edit messages, removes only
`think: false`, retains the 4096 cap and records the complete streams in
`m18-edit-comparison/`. Each call has a maximum 270-second inference deadline.
It applies no output and refuses an existing evidence directory. The source hashes are
checked again after the comparison. The exact dispatch descriptor is checked
before each call, and the inference client validates each response model name;
there is no post-final-call descriptor readback. Neither completed JSON
nor an in-memory syntax check establishes a useful repair.

This is an instrumented inference-entry replay, not a complete native worker
qualification or a product acceptance. M04 must verify the full managed worker
loop and preserve rejection gates. M10 must qualify exact reviewed production
sources, accumulated file reads, existing-file edits, new files, fixed host
checks and verified finish through the production wrapper/broker. M15 and M16
must establish real accepted work and the full unattended scheduled night.

Reuse decision: retain the existing inference client, metadata interface,
structured worker, receipts and qualification broker. The measured defect does
not justify a new dependency, new model or relaxed output/acceptance checks.
