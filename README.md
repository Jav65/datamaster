# DataMaster — Government Integration Control Layer

DataMaster is a judge-facing prototype for a real Batam logistics-permit story.
It shows how a new government workflow can reuse authoritative records that the
state already holds, how a constrained adapter can be proposed for a legacy
service, and how downstream applications can survive an upstream API contract
change without silently allowing AI to change production behavior.

> **OpenAPI describes an API. DataMaster manages the dependency and integration
> lifecycle across many APIs.**

The fictional demo company is **PT Selat Niaga Makmur**. It previously completed
a BP Batam land-allocation workflow and later applies for an IUK Logistik-style
business permit.

## What is implemented

- a stable `POST /api/resolve` contract using canonical concepts such as
  `business.nib`;
- a deterministic purpose policy that blocks `health.*` before any source call;
- per-field source provenance and a visible routing trace;
- a real Python `ast` scan of a legacy LMS fixture;
- generated adapter, OpenAPI, and Markdown documentation proposals;
- persisted proposal states and human approval gates;
- OSS v1 and v2 OpenAPI contracts plus observable mock-service version drift;
- deterministic contract diff and explicit dependency-graph impact analysis;
- optional live LLM semantic mapping with a labeled deterministic fallback;
- generic public-GitHub repository ingestion with bounded, non-executing source
  inspection and OpenAI Structured Outputs;
- source-file evidence and schema validation for every AI-documented API;
- a GitHub-App-style webhook boundary with optional HMAC verification;
- deterministic tests, demo reset, and a one-command launcher.

## Architecture

```text
Permit browser
    |
    | POST /api/resolve using canonical concept names
    v
DataMaster FastAPI server (:8000)
    |-- purpose policy (deterministic)
    |-- semantic registry + dependency graph (JSON demo state)
    |-- resolver + provenance
    |-- onboarding / change proposals + approval gates
    |
    v
Deterministic government API fixture (:9001)
    |-- Disdukcapil
    |-- OSS v1 or v2
    |-- DJP
    |-- AHU
    |-- BP Batam LMS bridge
    `-- SATUSEHAT (present, but forbidden for the permit purpose)
```

The browser receives no government credential. In this local prototype the
permit page and DataMaster API are served by the same FastAPI process. A
production permit backend would authenticate to DataMaster server-to-server.

## First-time setup

Run these commands in the repository root—the folder containing this README:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Line by line:

1. `python3 -m venv .venv` creates an isolated Python environment in `.venv`.
   It does not install or change system Python packages.
2. `source .venv/bin/activate` makes this terminal use that environment.
3. `python3 -m pip install -r requirements.txt` installs FastAPI, Uvicorn,
   HTTPX, Pydantic, dotenv support, and the optional Anthropic client used only
   by the contract-change proposal experiment.

An `ANTHROPIC_API_KEY` is **not required** for the permit, registry, onboarding,
diff, approval, webhook, reset, repository analysis, Playground, or tests. If
no Anthropic key is present, only the separate semantic contract-change
experiment uses its clearly labeled deterministic fallback.

To enable optional Anthropic-based semantic contract-change proposals:

```bash
export ANTHROPIC_API_KEY='your-key-here'
```

`export` sets the variable only for the current terminal and programs launched
from it. Do not commit real keys to the repository.

### Enable real repository documentation

The **Services → Connect GitHub repo** workflow and **Playground → Run query**
both require an OpenAI API key on the DataMaster server. Copy the example
configuration, add your key locally, and restart DataMaster:

```bash
cp .env.example .env
```

Then edit `.env` so these lines contain your server-side configuration:

```dotenv
OPENAI_API_KEY=your-openai-api-key
OPENAI_REPOSITORY_MODEL=gpt-5.6
OPENAI_QUERY_MODEL=gpt-5.6
```

`OPENAI_API_KEY` authenticates the server's calls to the OpenAI Responses API.
The two model variables make repository analysis and query routing independently
replaceable without code changes. Their defaults follow OpenAI's current model
guidance. The key is never returned by `/api/services`, embedded in HTML, or
sent to the browser. Never commit `.env`.

After restarting, open **Services**, click **Connect GitHub repo**, and enter a
public URL in this exact shape:

```text
https://github.com/owner/repository
```

DataMaster shallow-clones the repository, does not execute its code, excludes
dependencies and likely secret files, enforces file/byte/context limits, sends
the bounded source snapshot to the Responses API, validates the strict result,
and only then writes the service card to the registry. Reconnecting the same
canonical URL updates that generated service instead of creating a duplicate.

## Start the demo with one command

```bash
./run_demo.sh
```

The script:

1. resets DataMaster-owned demo state to OSS v1;
2. starts the mock government server at `http://127.0.0.1:9001`;
3. starts DataMaster at `http://127.0.0.1:8000`;
4. prints the console and permit URLs;
5. stops both processes when you press `Ctrl+C`.

Open:

- Console: `http://127.0.0.1:8000`
- Permit: `http://127.0.0.1:8000/permit`

If you prefer separate terminals, activate the same virtual environment in
both, then run:

```bash
# terminal 1
python3 -m uvicorn app.mock_gov:app --host 127.0.0.1 --port 9001

# terminal 2
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`python3 -m uvicorn module:object` imports the named Python module and serves
its FastAPI `app` object. `--host 127.0.0.1` keeps this unauthenticated demo
local to the machine; `--port` gives each process a different TCP port.

## Exact 4–5 minute judge sequence

Start with a clean `./run_demo.sh`, or click **Reset demo** in the console.

1. Open `/permit`.
2. Point out that NIB, NPWP, company deed, director identity, and the prior land
   record are required again, while warehouse evidence, warehouse plan, and the
   requested logistics activity are genuinely new.
3. Click **Reuse verified government records**.
4. Show each resolved value and source, expand **How DataMaster resolved this**,
   and point out **SATUSEHAT was NOT queried**.
5. Open the console and click **Onboarding**.
6. Click **Scan codebase**. Expand the adapter, OpenAPI, and generated-docs
   previews. The displayed function metadata comes from a real Python AST scan.
7. Click **Approve integration**. The adapter becomes active only after the
   deterministic fixture tests pass.
8. Click **Changes**, then **Simulate upstream OSS merge**.
9. Show the deterministic path/field diff, affected canonical concepts,
   affected permit consumer, semantic proposal, and waiting approval gate.
10. Optional: click **Observe degraded permit**, then try the same reuse button.
    OSS v1 now returns `410`, and DataMaster blocks an unsafe automatic remap.
11. Return to **Changes** and click **Approve & test**.
12. Show the contract tests, registry update, regenerated docs, and unchanged
    stable contract message.
13. Click **Prove permit still works**, then click the same reuse button. The
    permit request is unchanged while the approved adapter now reads OSS v2's
    `business_identification_number` field.

End with:

> Connect government systems once through a stable semantic layer. DataMaster
> helps discover integrations, reuse authoritative records, and safely maintain
> those integrations as upstream APIs evolve.

## Run the automated tests

```bash
python3 -m unittest discover -v
```

`unittest discover` finds files named `test*.py`, runs every `unittest.TestCase`,
prints each test name because of `-v`, and exits non-zero if any assertion fails.
The 12 deterministic tests use temporary state and do not require either server,
an LLM key, or internet access.

The console's **Tests** tab runs an equivalent isolated, judge-readable check
suite through `POST /api/demo/tests`.

## Important API endpoints

| Method and path | Responsibility |
|---|---|
| `POST /api/resolve` | Resolve approved canonical concepts under a purpose policy. |
| `POST /api/onboarding/scan` | AST-scan the constrained legacy fixture and create a pending proposal. |
| `POST /api/onboarding/{id}/approve` | Run fixture tests, persist artifacts, then activate the proposed integration. |
| `POST /api/changes/simulate-oss-v2` | Exercise the same change pipeline as the webhook without internet. |
| `POST /api/changes/{id}/approve` | Test a candidate mapping, update the registry, and regenerate docs. |
| `POST /api/repositories/connect` | Clone a public GitHub repo and generate validated API documentation with OpenAI. |
| `POST /api/github/webhook` | Receive a narrow GitHub push or merged-PR event. |
| `POST /api/demo/reset` | Restore OSS v1, initial registry, empty reviews, and remove generated demo artifacts. |

To require webhook signatures, set a secret before starting DataMaster:

```bash
export GITHUB_WEBHOOK_SECRET='choose-a-random-demo-secret'
```

When configured, the endpoint verifies GitHub's `X-Hub-Signature-256` HMAC with
constant-time comparison. When absent, unsigned local webhook demonstrations
are accepted; this is a documented demo convenience, not a production default.

## Files and responsibilities

```text
app/
├── main.py              # HTTP boundary and UI routes
├── resolver.py          # stable canonical concept resolution + provenance
├── policy.py            # deterministic purpose/field authorization
├── state_store.py       # atomic JSON persistence for local review state
├── contract_diff.py     # deterministic OpenAPI structural diff
├── semantic_analysis.py # optional AI proposal + offline fallback
├── change_manager.py    # impact analysis, tests, approval, activation
├── onboarding.py        # constrained AST scan and adapter proposal
├── github_webhook.py    # signature and event validation
├── repository_scanner.py # bounded GitHub clone + OpenAI API documentation
├── demo_checks.py       # isolated checks shown in the console
├── agent.py             # OpenAI Responses API query-routing Playground
└── mock_gov.py          # deterministic mock agencies and OSS drift
contracts/               # OSS v1/v2 OpenAPI documents
fixtures/legacy_lms/     # code-only legacy service; initially no HTTP/OpenAPI
state/                   # semantic registry, dependencies, and review state
frontend/                # static console and permit pages
tests/test_demo.py       # deterministic acceptance tests
run_demo.sh              # clean reset + two local servers
```

## Real prototype behavior versus simulation

Real prototype behavior:

- purpose authorization happens before source execution;
- the permit resolver calls multiple deterministic HTTP APIs;
- OSS v1 actually stops serving normally after the v2 switch;
- registry mappings determine which upstream path and field are read;
- legacy Python is parsed with `ast` and its function signature is discovered;
- OpenAPI structure is diffed with ordinary deterministic code;
- dependency impact comes from an explicit graph;
- proposals persist in JSON and cannot activate without approval;
- candidate mappings and legacy adapters run deterministic tests;
- approved artifacts and docs are written to `generated/`;
- public GitHub repositories are actually cloned and their bounded source is
  analyzed by OpenAI when `OPENAI_API_KEY` is configured;
- AI documentation must match a strict schema and cite inspected source files
  before it is persisted;
- the stable permit request works unchanged after the approved OSS v2 mapping.

Simulated external infrastructure:

- government agencies and records are deterministic local fixtures;
- the local merge button simulates delivery from a GitHub App, while the real
  webhook-shaped endpoint is also implemented;
- approval actor identity is a fixed demo reviewer;
- the generated adapter executes a single allow-listed Python repository
  function in-process; it is not a universal arbitrary-code deployment system;
- JSON files stand in for a transactional database and national audit system.

## Known limitations and production path

The repository connector is genuine rather than hard-coded, but this whole app
is still a hackathon prototype—not a production national integration platform.
It has no user authentication, tenant authorization, rate limiting, durable job
queue, managed secrets, PostgreSQL transaction boundary, malware scanning,
multi-node coordination, or tamper-evident audit log. Its JSON state is suitable
for one local process only. The synchronous connector accepts public
`github.com` repositories up to 20,000 files/50 MB and sends at most 180 UTF-8
files/180,000 characters for analysis.

A production version would introduce authenticated server-to-server calls,
separate platform-administration and data-access permissions, PostgreSQL-backed
review/audit state, secrets management, mTLS or equivalent service identity,
idempotent webhook processing, queued contract analysis, observability, and a
staged deployment/rollback mechanism. AI would remain a proposal mechanism;
authorization, validation, tests, and activation would remain deterministic
gates.
