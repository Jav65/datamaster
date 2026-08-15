# BP Batam Service API — Dummy Repository

This repository is a self-contained teaching fixture for the DataMaster demo. It
models a small BP Batam FastAPI service whose endpoints expose land records,
import masterlists, UWTO status, and permit validation.

The records are fictional and the service is not connected to a real BP Batam
system. Do not use it for production decisions.

## Repository structure

```text
.
├── .github/workflows/tests.yml
├── .gitignore
├── README.md
├── pyproject.toml
├── src/bp_batam_api/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   └── repository.py
└── tests/test_api.py
```

## Run locally

Create and activate a virtual environment, then install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
```

Start the API from the repository root:

```bash
python3 -m uvicorn bp_batam_api.main:app --reload --port 9100
```

Open the generated OpenAPI interface at `http://127.0.0.1:9100/docs`.

Run the tests:

```bash
python3 -m pytest
```

## DataMaster demo

DataMaster's local “Connect GitHub repository” action maps
`https://github.com/alexgeraldhandoko/bp-batam.git` to this repository fixture.
It parses the FastAPI route decorators from `src/`, discovers the API method,
path, and description, and updates the Services catalog. Structural fields are
always derived deterministically from code; an optional language model may
improve descriptions when an API key is configured.
