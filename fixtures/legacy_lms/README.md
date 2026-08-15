# Legacy LMS fixture

This folder represents a narrow, realistic legacy integration target. It has an
internal Python repository function and deterministic land records, but it has
no public HTTP endpoint and no OpenAPI document.

DataMaster's onboarding prototype scans `repository.py` with Python's `ast`
module, creates a constrained adapter proposal, derives OpenAPI and readable
documentation, runs a fixture contract check, and waits for human approval.
