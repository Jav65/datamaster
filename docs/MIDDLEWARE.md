# Using DataMaster as a Middleware

DataMaster sits between your application (a permit form, a hospital intake
screen, a school admission portal) and the government APIs that master the
data. Your app asks one question in one shape; DataMaster figures out which
agencies to call, in what order, and returns a JSON object with exactly the
field names your app asked for.

```
┌────────────┐   {input, fields}   ┌────────────┐  chained HTTP  ┌───────────┐
│  Your app   │ ──────────────────▶ │ DataMaster │ ─────────────▶ │ Dukcapil  │
│ (any stack) │ ◀────────────────── │   :8000    │ ─────────────▶ │ DJP / OSS │
└────────────┘    JSON, your keys   └────────────┘ ─────────────▶ │ BP Batam… │
                                                                  └───────────┘
```

Three properties make it a middleware rather than just a proxy:

1. **Field-name translation.** You ask for `bloodType`; SATUSEHAT stores
   `blood_type` inside a nested object. The mapping is inferred, not coded.
2. **Chaining.** You supply `{name, phone}`; DataMaster resolves the NIK
   first, then uses it for every downstream call. Your app never needs to
   know the dependency graph.
3. **Config-driven registry.** Adding an agency = adding a `config.json`
   entry. No code change, no redeploy of your app.

---

## 1. The core contract

### `POST /api/query`  (blocking — use this from backends and forms)

Request:
```json
{
  "input":  "{name: Budi, no: +62811770909}",
  "fields": "{bloodType, allergies, lastCheckupDate}"
}
```

- `input` — loose, JSON-ish identification of the subject. Key names are
  flexible (`no`, `hp`, `telp` → phone; `nama` → name). Values are what you
  have; DataMaster works out what they mean.
- `fields` — the exact output keys you want back, in `{a, b, c}` form.
  These names are yours: camelCase, Indonesian, whatever your app uses.

Response:
```json
{
  "answer": {
    "bloodType": "A+",
    "allergies": ["seafood (shellfish)"],
    "lastCheckupDate": "2025-09-08"
  },
  "raw": "…model output…",
  "parse_error": null,
  "called_tools": ["dukcapil_getNIK", "satusehat_getHealth"],
  "trace": [ …every request/response step… ]
}
```

Rules your app can rely on:
- `answer` keys are **exactly** the names you requested — no more, no fewer
  (occasionally plus `_note` when something needs explaining).
- Unavailable data comes back as `null`, never invented.
- `answer` is `null` when the model's output couldn't be parsed as JSON;
  `parse_error` says why. **Always handle this case** — the router is an
  LLM, and prompt-enforced JSON is highly reliable but not grammar-guaranteed.
- `called_tools` is your audit line: which agencies were touched to answer.
- `trace` contains full request/response pairs — log it for compliance,
  hide it from end users.

### `GET /api/query/stream?input=…&fields=…`  (SSE — use for live UIs)

Same semantics, but every step arrives as a Server-Sent Event the moment it
happens: `thinking`, `call`, `result`, then `final` (with `json`, `raw`,
`parse_error`). Use this when you want the user to watch the routing, like
the Playground does.

---

## 2. Integration examples

### curl
```bash
curl -s -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"input": "{name: John Doe, no: +62838292938}",
       "fields": "{npwp, companyName, uwtoStatus}"}' | jq .answer
```

### JavaScript — form autofill (the pattern behind `/permit`)
```js
// Field ids on your form double as the requested output keys.
const FIELDS = ["nik", "npwp", "companyName", "nib", "plot", "uwtoStatus"];

async function autofill(name, phone) {
  const r = await fetch("http://localhost:8000/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input: `{name: ${name}, phone: ${phone}}`,
      fields: `{${FIELDS.join(", ")}}`,
    }),
  });
  const d = await r.json();
  if (!d.answer) throw new Error(d.parse_error ?? "no data");
  for (const f of FIELDS) {
    const el = document.getElementById(f);
    if (d.answer[f] != null) { el.value = d.answer[f]; el.dataset.source = "datamaster"; }
    else el.dataset.source = "manual";           // ask the user
  }
  return d.called_tools;                          // show as provenance chips
}
```

### Python — backend-to-backend
```python
import httpx

def fetch_citizen(fields: list[str], **identity) -> dict:
    input_str = "{" + ", ".join(f"{k}: {v}" for k, v in identity.items()) + "}"
    r = httpx.post("http://localhost:8000/api/query", json={
        "input": input_str,
        "fields": "{" + ", ".join(fields) + "}",
    }, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d["answer"] is None:
        raise ValueError(f"DataMaster parse failure: {d['parse_error']}")
    return d["answer"]

data = fetch_citizen(["npwp", "riskLevel"], name="Siti Rahma", hp="+628117701234")
```

---

## 3. Registering agencies (`config.json`)

```json
{
  "id": "ahu_getDeed",
  "api": "http://localhost:9001/ahu/getDeed",
  "method": "GET",
  "desc": "AHU (Ministry of Law) company registry. Given a NIK of a company founder/director, returns the deed of establishment (akta) number, SK Kemenkumham decree, notary, and deed date. Use for legal-entity fields on permit forms.",
  "params": [
    { "name": "nik", "type": "string", "required": true, "desc": "Director's 16-digit NIK" }
  ],
  "returns": "deed_number, sk_kemenkumham, notary, deed_date, company_name"
}
```

Field-by-field:
- **`id`** — becomes the tool name; appears in `called_tools` and traces.
- **`api` + `method`** — the executor calls exactly this. GET sends params
  as query string; anything else sends a JSON body.
- **`desc` — this is the routing logic.** Write it *for the agent*: what
  the API knows, what it needs, and when to use it ("Call this FIRST when…",
  "Use for legal-entity fields…"). A vague description produces vague routing.
- **`params`** — become the tool's input schema; `required` is enforced by
  the model's tool-calling, and descriptions guide value extraction.
- **`returns`** — tells the agent what it will get, so it can plan chains
  ("this gives me a NIB, which bpbatam_getMasterlist needs").

Manage entries via `GET/PUT /api/config` or the Config tab. Changes apply on
the next query — the registry is read per request.

### Chaining is emergent, not configured
There is no pipeline definition anywhere. The agent chains
`dukcapil_getNIK → oss_getNIB → bpbatam_getMasterlist` because the
descriptions say what each needs and produces. This is the point of the
architecture: **adding an agency doesn't require editing any workflow.**

---

## 4. Testing your integration

`POST /api/tests/run` asserts three things per case:

```json
{
  "tests": [{
    "name": "Permit autofill pulls the full chain",
    "input": "{name: John Doe, phone: +62838292938}",
    "fields": "{nik, npwp, companyName, deedNumber, plot}",
    "expect": ["dukcapil_getNIK", "djp_getNPWP", "oss_getNIB",
               "ahu_getDeed", "bpbatam_getMasterlist"],
    "forbid": ["satusehat_getHealth"],
    "expect_values": { "companyName": "PT Selat Niaga Makmur" }
  }]
}
```

- `expect` — tools that MUST be called (completeness)
- `forbid` — tools that must NOT be called (**data minimization** — a permit
  form has no business touching health records; make that a failing test,
  not a hope)
- `expect_values` — exact values in the answer (correctness end-to-end)

Because routing is LLM-driven and non-deterministic, run suites repeatedly
and watch pass *rates*, not single runs. CLI equivalent:
`python -m tests.test_routing`.

---

## 5. Operational notes & limitations (demo scope)

- **Auth to agencies:** real government APIs need OAuth/API keys. The natural
  extension is a `headers` or `auth` block per config entry, with secrets in
  env vars — never in `config.json`.
- **Auth to DataMaster itself:** the demo has none. Anything that can reach
  port 8000 can query any registered citizen. In production this needs an
  API gateway, per-client scopes (which fields/agencies a client may ask
  for), and consent checks — the `forbid` test pattern is the seed of that
  policy layer.
- **PII and logging:** traces contain full personal data. Encrypt at rest,
  redact in logs, retention limits.
- **Latency & cost:** every query is 1–3 LLM rounds plus the agency calls;
  expect seconds, not milliseconds. Cache identity resolution (name+phone →
  NIK) where policy allows.
- **Determinism:** same query may occasionally route differently. The test
  suite is the control loop; for hard guarantees on output shape, move to
  the API's structured-output features.
- **Timeouts:** the executor uses 10s per agency call and up to 6 agent
  rounds; tune both in `app/agent.py`.
