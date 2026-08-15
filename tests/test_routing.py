"""
CLI routing tests — same assertions as the frontend Tests tab.

Each test gives:
  input          — loose JSON-ish subject, flexible key names
  fields         — requested output fields
  expect/forbid  — tools that must / must not be called
  expect_values  — exact values required in the JSON answer

Run (mock_gov on :9001, OPENAI_API_KEY in .env):
    python -m tests.test_routing
"""

from app.agent import run_agent

TESTS = [
    {
        "name": "Multi-agency, exact values",
        "input": "{name: John Doe, no: +62838292938}",
        "fields": "{npwp, bloodType}",
        "expect": ["dukcapil_getNIK", "djp_getNPWP", "satusehat_getHealth"],
        "forbid": [],
        "expect_values": {"npwp": "09.254.294.3-217.000", "bloodType": "O+"},
    },
    {
        "name": "Three-hop chain (identity -> business -> FTZ)",
        "input": "{name: John Doe, phone: +62838292938}",
        "fields": "{companyName, uwtoStatus}",
        "expect": ["dukcapil_getNIK", "oss_getNIB", "bpbatam_getMasterlist"],
        "forbid": [],
        "expect_values": {
            "companyName": "PT Selat Niaga Makmur",
            "uwtoStatus": "Paid through 2027",
        },
    },
    {
        "name": "Health only — must NOT touch tax/business",
        "input": "{nama: Budi Santoso, hp: +62811770909}",
        "fields": "{bloodType, allergies}",
        "expect": ["dukcapil_getNIK", "satusehat_getHealth"],
        "forbid": ["djp_getNPWP", "oss_getNIB", "bpbatam_getMasterlist"],
        "expect_values": {"bloodType": "A+"},
    },
    {
        "name": "Permit autofill — full chain incl. AHU, no health",
        "input": "{name: John Doe, phone: +62838292938}",
        "fields": "{nik, npwp, companyName, deedNumber, plot}",
        "expect": ["dukcapil_getNIK", "djp_getNPWP", "oss_getNIB", "ahu_getDeed", "bpbatam_getMasterlist"],
        "forbid": ["satusehat_getHealth"],
        "expect_values": {"companyName": "PT Selat Niaga Makmur",
                          "deedNumber": "AHU-0045821.AH.01.01.2026"},
    },
    {
        "name": "Graceful gap — no NPWP -> null",
        "input": "{name: Budi, no: +62811770909}",
        "fields": "{npwp}",
        "expect": ["dukcapil_getNIK", "djp_getNPWP"],
        "forbid": [],
        "expect_values": {"npwp": None},
    },
]


def check_values(answer, expected):
    problems = []
    if not expected:
        return problems
    if answer is None:
        return [f"expected values {expected} but answer was not valid JSON"]
    for k, v in expected.items():
        if k not in answer:
            problems.append(f"missing field '{k}'")
        elif answer[k] != v:
            problems.append(f"'{k}': expected {v!r}, got {answer[k]!r}")
    return problems


def main() -> int:
    passed = 0
    for t in TESTS:
        out = run_agent(t["input"], t["fields"])
        called = out["called_tools"]
        missing = [x for x in t["expect"] if x not in called]
        forbidden = [x for x in t["forbid"] if x in called]
        value_problems = check_values(out["answer"], t.get("expect_values", {}))
        ok = not missing and not forbidden and not value_problems
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {t['name']}")
        print(f"       called: {called}")
        if missing:
            print(f"       missing: {missing}")
        if forbidden:
            print(f"       forbidden called: {forbidden}")
        for p in value_problems:
            print(f"       value: {p}")
        print(f"       answer: {out['answer']}")
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
