# Reuse decisions

Bounded repair decomposition reuses the existing transactional HistoryStore,
manager-recovery lineage, Ralphy/local-code worker, tool-free local supervisor,
configured focused runners and full candidate/delivery gates. Its small original
state machine binds those existing interfaces without another agent framework,
dependency, worker client or copied upstream implementation.

For SecondApp admission, the existing mature ExampleApp supervisor is extracted into
one shared `project_backend.py`, retaining per-project bindings and tests rather
than maintaining a second copy of orchestration policy. Standard-library
`importlib` gives each backend independent state/globals without a new framework.
The exact Autoissue/Ralphy runtime is now shared under `ralphy/`; all 26 files in
its retained provenance manifest match their original recorded hashes. The MIT
licence and immutable source repository/revision/file notices are preserved.
Only the separate local `run.sh` adapter uses the project-supplied worker check.
No vendor source bytes were modified.

SecondApp reuses its existing locked Jest, Playwright, TypeScript and application
dependencies in a separate private tree. The official checksum-verified Node24
runtime retains its licence; neither dependencies nor runtime binaries are
added to these source repositories. No application dependency was introduced.

Midnight's standing OPEN_SOURCE.md policy applies across admitted projects. The existing pinned Autoissue/Ralphy adapter remains in use, with its original provenance and notices unchanged.

For the 31 October worker-quality and dashboard release, the Python standard library supplies hashing, JSON, CSV, HTTP request handling and process interfaces. The shared quality helper is original orchestration glue around existing checkpoint/evidence formats. The read-only dashboard uses `ThreadingHTTPServer` with fixed routes, explicit loopback binding, Host/Origin checks, no action endpoints and no arbitrary-file handler. A separate web framework/frontend dependency would add installation and maintenance cost for this small private inspection surface. The dashboard is not intended for public hosting.

Primary references reviewed: [http.server](https://docs.python.org/3/library/http.server.html), [selectors](https://docs.python.org/3/library/selectors.html), [subprocess](https://docs.python.org/3/library/subprocess.html). Selectors/Popen are supplied as implementation references for the project's bounded-tool repair tasks; a reference is not an installed external-tool integration. No third-party source was copied for these helpers, and no new package was installed.

For owner-requested 3D graphics, the dashboard vendors upstream **Three.js r187**, commit **7e89746a687bb88271253698fa108b1dd59825d0**, unchanged build/three.module.js, build/three.core.js and the MIT licence. dashboard/vendor/UPSTREAM.json records the exact source URLs, hashes and sizes; THREE-LICENSE.txt retains copyright and licence terms. This avoids reimplementing a WebGL renderer and avoids runtime CDN requests. Update only through coordinator source/licence review and repeat browser/graphics checks. No upstream scripts or downloaded executables were run. The native notification adapter uses macOS's existing osascript interface; no notifier package or external push service was installed.

The live Mac/model telemetry adapter reuses macOS's installed Mach/process APIs, `vm_stat`, `sysctl`, `ioreg`, `ps`, Foundation thermal state and Ollama's documented read-only `GET /api/ps`. The existing Python standard library supplies ctypes, plist/JSON parsing, the bounded thread and HTTP feed; Canvas2D draws small recent-history charts. Adding a monitoring daemon, psutil or a charting dependency would introduce installation and runtime cost for measurements already available on this Mac. No third-party implementation was copied or new package installed. Native SDK declarations establish the ABI; absent GPU/platform counters remain unavailable. Ollama references: [running-model API](https://github.com/ollama/ollama/blob/main/docs/api.md#list-running-models), [OpenAPI schema](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml). These counters do not establish remote cloud hardware utilisation or live token throughput.


The durable scheduler reuses Python's existing SQLite/flock/process facilities,
the installed licensed Ralphy/Autoissue worker, shared cloud policy and checked
GitDelivery. No additional orchestration dependency was introduced. The small
local action journal and guardian are original integration code because those
existing components do not jointly own Midnight's schedule, per-project signed
acceptance, crash receipts and private source-adoption boundary.
