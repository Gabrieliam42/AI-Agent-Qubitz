# Optional MCP Capabilities

Each variant exposes an isolated optional MCP router and session state. The normal
launcher composes shared implementation into that variant's private namespace;
standalone exports include the composed code. Existing native tools remain the
first choice, and optional servers are never attached to every model request.

## Configuration

`mcp_capabilities.example.json` lists supported providers, all disabled. Configure
the providers you want in the runtime directory's `.qubitz/mcp_capabilities.json`.
This is operator-owned configuration, not an agent-editable workspace instruction.
Missing configuration leaves existing behavior unchanged. Restart the task after
changing configuration. No server packages are installed by this feature.

- Use an already-installed executable or an explicit interpreter plus installed
  server script; shell and package-runner launch commands are rejected.
- `args` is an argv list, never a shell command. `{workspace}` means the current
  task workspace; `{runtime}` means the directory containing the variant.
- `env` maps child variable names to existing parent environment-variable names.
  Do not put credentials in this JSON. Only mapped variables and a small process
  environment are forwarded. For HTTP, `bearer_token_env` names a token variable.
- `runtime_env` accepts only provider-specific non-secret settings: Serena's
  `SERENA_HOME`, Playwright's `PLAYWRIGHT_BROWSERS_PATH`, and Sequential Thinking's
  `DISABLE_THOUGHT_LOGGING` (`true` or `false`). Cache paths must resolve inside
  the runtime directory. Other variables, including credentials, use `env`.
- `cost_policy` must be `free` or `self_hosted`. Confirm the service/account really
  has no billing enabled. Open-source clients do not make hosted API calls free.
  Qubitz does not purchase credits or switch to a paid fallback when a quota fails.
- `workspaces` can restrict a provider to exact absolute workspace paths; DBHub
  requires this binding. Use WSL paths when running Qubitz in WSL.

## Routing And Permissions

`discover_mcp_capability()` returns configuration metadata without starting a
server. A named `capability` returns permitted tool names; adding `tool_name`
returns only that live tool's schema. Invoke it using `call_project_mcp_tool` with
`server_reference="capability:<id>"` and the exact advertised arguments.

Relevant requests expose only these small discovery/call interfaces, not all
third-party schemas. Current native fast paths, dependency-install transactions,
and focused transactional recovery remain unchanged. Ordinary local questions do
not acquire an online-research requirement merely because a provider is enabled.

Sessions start lazily, retain state across calls within one task/MCP-host session,
and close at task exit, cancellation, or failure. They are not shared across
variants, workspaces, or GUI turns. Failed side-effect calls are not automatically
replayed because their outcome can be unknown. Private server stderr logs are
under `.cache/mcp_capabilities/`; logs may contain server-provided sensitive data.

Read-only and Plan modes reject nested browser actions and remote writes. Full
Access still requires a task requesting those actions. Unknown tools are denied,
regardless of their server-supplied annotations. No provider can use this adapter
to bypass Qubitz's transactional filesystem editing. This is a trusted-server
policy layer, not an OS sandbox for malicious server executables.

Offline mode blocks network-capable providers before startup, including locally
hosted browser/research/database services that could themselves contact networks.
Local native tools and local-only Serena/Sequential Thinking remain available.

## Providers

- [Serena](https://github.com/oraios/serena): semantic navigation only, scoped to
  the current workspace in planning mode. Install separately from Qubitz's ML
  environment because its dependency pins differ. Language servers may need
  separate setup; ensure they are available before offline use.
- [GitHub](https://github.com/github/github-mcp-server): repo, PR, issue, and
  Actions reads, with lockdown filtering. Remote mutations additionally require
  `allow_remote_mutations: true`, a suitable token, Full Access, and user intent.
  Lockdown is a content filter, not a prompt-injection-proof security boundary.
- [Context7](https://github.com/upstash/context7): task-relevant external docs in
  Online mode. Optional authentication uses `bearer_token_env`. Respect version
  requirements and free-account limits; never present missing results as verified.
- [Playwright MCP](https://github.com/microsoft/playwright-mcp): conditional
  browser interaction, snapshots, console, and network records in an isolated
  browser profile. Arbitrary JavaScript execution and file uploads are not exposed.
  The Node MCP server is separate from Python `playwright`. Its browser build must
  match its own Playwright version; do not assume Python's Chromium is compatible.
- [DBHub](https://github.com/bytebase/dbhub): readonly inspection/querying only,
  with explicitly bound workspace and `read_only_credentials_confirmed: true`.
  Requires a patched server (at least 0.22.6), readonly credentials, and TOML
  `execute_sql` entries with `readonly = true` and `max_rows` between 1 and 1000
  covering every source. Set source `query_timeout` where supported. An old
  [read-only bypass](https://github.com/bytebase/dbhub/security/advisories/GHSA-mwwr-p57h-56pf)
  is why a leading `SELECT` or annotation is not sufficient. Database writes
  remain unavailable through this adapter.
- [Firecrawl](https://github.com/firecrawl/firecrawl-mcp-server): fallback for
  genuinely needed crawling/extraction after native fetch/search is inadequate.
  Self-hosted configuration requires `FIRECRAWL_API_URL`; otherwise separately
  confirm a free hosted account. Credentialed local-file upload is not exposed.
- [Brave](https://github.com/brave/brave-search-mcp-server): optional search
  alternative, not a mandatory dependency. Only enable with a verified free
  entitlement; having an API key alone does not establish that calls are free.
- [Sequential Thinking](https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking):
  explicit-use experiment, never automatically selected for ordinary reasoning.

Python `playwright==1.62.0` is in `requirements.txt`. Its Chromium runtime is
installed separately with `.venv/bin/python -m playwright install chromium`.
Chromium is not a Python requirement entry.

## Verified Local Setup

The approved WSL setup lives under `.cache/mcp/`, outside the ML virtual environment.
The active `.qubitz/mcp_capabilities.json` enables these on demand, not at GUI startup:

- Serena 1.7.0 with isolated Python 3.12 and Pyright 1.1.403: Python symbol lookup
  verified; explicit Node launch avoids implicit Python downloads. Other language
  servers still need their own setup. Startup measured about 100 seconds here;
  prefer native reads/searches when sufficient.
- Context7: live documentation lookup verified through its public MCP endpoint.
- Playwright MCP 0.0.80: matching Chromium 1243; local-page navigation, snapshot,
  console capture, and browser shutdown verified.
- Sequential Thinking 2026.8.31: invocation verified; thought logging disabled.

Node 22.23.2 is isolated alongside these servers. GitHub MCP 1.12.0, DBHub 1.2.3,
Firecrawl MCP 3.24.0, and Brave Search MCP 2.1.3 are installed but disabled.
GitHub needs a scoped token; DBHub needs a workspace-bound readonly connection;
Firecrawl needs a self-hosted endpoint or verified free account; Brave needs a
verified free entitlement and key. No credentials or paid accounts were created.
DBHub separately passed a disposable SQLite read and rejected a DELETE without
changing the data; this does not configure access to any user's database.

Installation hashes and live result receipts are under `.cache/mcp/`. These local
Linux paths are machine setup, not a promise that a fresh Windows install already
has the servers. All variants still contain independent adapters.
