"""Deterministic tests for the OpenAI Responses API query-routing loop."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from app.agent import build_tools, run_agent


TEST_API = {
    "id": "dukcapil_getNIK",
    "api": "http://localhost:9001/dukcapil/getNIK",
    "method": "GET",
    "desc": "Resolve a citizen identity from name and phone.",
    "params": [
        {"name": "name", "type": "string", "required": True, "desc": "Legal name"},
        {"name": "phone", "type": "string", "required": True, "desc": "Phone"},
    ],
    "returns": "nik",
}


class OpenAIQueryAgentTests(unittest.TestCase):
    def test_function_schema_is_strict(self):
        tool = build_tools([TEST_API])[0]
        self.assertEqual(tool["type"], "function")
        self.assertTrue(tool["strict"])
        self.assertFalse(tool["parameters"]["additionalProperties"])
        self.assertEqual(tool["parameters"]["required"], ["name", "phone"])

    def test_tool_call_output_is_returned_to_responses_api(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "output": [
                            {
                                "type": "function_call",
                                "call_id": "call_test_1",
                                "name": "dukcapil_getNIK",
                                "arguments": json.dumps(
                                    {"name": "John Doe", "phone": "+62838292938"}
                                ),
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"nik": "2171012507890001"}),
                                }
                            ],
                        }
                    ]
                },
            )

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-key"}),
            patch(
                "app.agent.execute_api",
                return_value=(200, {"nik": "2171012507890001"}, 3.2),
            ),
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
        ):
            result = run_agent(
                "{name: John Doe, no: +62838292938}",
                "{nik}",
                apis=[TEST_API],
                openai_client=client,
            )

        self.assertEqual(result["answer"], {"nik": "2171012507890001"})
        self.assertEqual(result["called_tools"], ["dukcapil_getNIK"])
        tool_outputs = [
            item
            for item in requests[1]["input"]
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual(tool_outputs[0]["call_id"], "call_test_1")


if __name__ == "__main__":
    unittest.main()
