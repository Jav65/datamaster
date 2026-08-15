# DataMaster

Config-driven government data broker. One query, one agent, zero re-keying.

```
INPUT:  {name: Budi, no: +62811770909}
FIELDS: {bloodType, allergies, lastCheckupDate}
```

The agent reads `config.json`, decides which agency APIs to call and in what order, and returns a JSON object with exactly the field names you asked for.

## Repos

| Repo | Purpose | Default port |
|---|---|---|
| **datamaster** (this repo) | Broker, agent, frontend console | 8000 |
| **batam-business** | Fake government APIs for local dev | 9001 |
| **batam-permit** | BP Batam business-permit form | 8080 |

All three read their configuration from a `.env` file in their own folder. No host or port is hardcoded in source.

## Layout

```
datamaster/
├── app/
│   ├── agent.py       # config → tools → LLM routing loop → HTTP calls
│   └── main.py        # /api/query, SSE trace stream, config CRUD
├── frontend/
│   └── index.html     # playground + config editor (served at /)
├── docs/
│   └── MIDDLEWARE.md  # integration guide
├── config.json        # API registry — edit via UI or by hand
├── run.py             # entry point — reads HOST/PORT from .env
├── .env.example       # committed template
├── .env               # your local values (gitignored)
├── requirements.txt
└── README.md
```

## Quickstart

```bash
# 1. Mock government APIs (separate repo)
cd batam-business && pip install -r requirements.txt
cp .env.example .env
python run.py                       # :9001

# 2. DataMaster (this repo, second terminal)
cd datamaster && pip install -r requirements.txt
cp .env.example .env                # then add your ANTHROPIC_API_KEY
python run.py                       # :8000

# 3. Permit form (third repo, third terminal — optional)
cd batam-permit && pip install -r requirements.txt
cp .env.example .env
python serve.py                     # :8080
```

Open [http://localhost:8000](http://localhost:8000) for the console.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. The agent will not start without it. |
| `GOV_API_BASE` | `http://localhost:9001` | Where the agency APIs live. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Where this server binds. |
| `PERMIT_URL` | `http://localhost:8080` | `GET /permit` redirects here. Blank disables the route. |
| `CORS_ORIGINS` | `*` | Comma-separated origins allowed to call `/api/*`. |

`config.json` stores endpoints as `${GOV_API_BASE}/dukcapil/getNIK`. The
placeholder is expanded when the call is made, not when the config is loaded —
so saving from the Config tab round-trips without baking in a host. You will
see the literal `${GOV_API_BASE}` in the UI; that is expected.

Real environment variables take precedence over `.env`, so a container can set
`GOV_API_BASE` without the file being consulted.

## Docs are generated, not written

`docs/api/openapi.json` is produced from the route definitions in
`app/main.py`. Never edit it by hand.

```bash
python scripts/gen_openapi.py           # regenerate
python scripts/gen_openapi.py --check   # what CI runs
```

CI fails any PR where the committed spec differs from a fresh generation, so
code and docs cannot drift apart. `oasdiff` then compares the spec against
`main` and fails the PR on breaking changes. On merge, the spec is rendered
with Redocly and published to GitHub Pages.

`fastapi` and `pydantic` are pinned exactly in `requirements.txt` because
their versions shape the generated schema. Bumping either will legitimately
change the spec — bump deliberately, regenerate, and commit.

## How it works

1. `agent.load_config()` reads `config.json`. Each entry's `desc` field becomes an Anthropic tool description — **the description is the routing logic**.
2. The agent loop (max 6 rounds): LLM picks tool calls → `execute_api()` makes real HTTP requests to the configured endpoints → results fed back → repeats until the LLM produces a text answer.
3. Every step is emitted via `on_trace`, forwarded as Server-Sent Events by `/api/query/stream` for the live trace UI.

## Adding a real agency

Add an entry to `config.json` (or use the Config tab in the UI):

```json
{
  "id": "my_agency_getData",
  "api": "https://api.my-agency.go.id/getData",
  "method": "GET",
  "desc": "My Agency. Given a NIK, returns X and Y. Call this when the query needs X.",
  "params": [
    { "name": "nik", "type": "string", "required": true, "desc": "16-digit national ID" }
  ],
  "returns": "x, y"
}
```

The `desc` field is what the agent reads to decide when and whether to call this API. Write it for the agent: what the API knows, what it needs, and when to prefer it. Changes take effect on the next query — no restart needed.

## API reference

### `POST /api/query` — blocking

```json
{ "input": "{name: John Doe, phone: +62838292938}", "fields": "{npwp, companyName}" }
```

Response:
```json
{
  "answer": { "npwp": "09.254.294.3-217.000", "companyName": "PT Selat Niaga Makmur" },
  "called_tools": ["dukcapil_getNIK", "djp_getNPWP", "oss_getNIB"],
  "trace": [ ... ],
  "raw": "...",
  "parse_error": null
}
```

- `answer` keys match exactly the names you requested.
- Unavailable fields come back as `null`, never invented.
- `called_tools` is your audit trail.

### `GET /api/query/stream?input=…&fields=…` — Server-Sent Events

Same semantics; each routing step arrives as an event in real time. Event types: `thinking`, `call`, `result`, `final`, `error`.

### `GET /api/config` / `PUT /api/config`

Read or replace the full agency registry.