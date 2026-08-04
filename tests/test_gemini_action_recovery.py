"""Regression coverage for Vertex Gemini's malformed function-call recovery."""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from jarvis.agent.brain import BrainError, GeminiVertexBrain
from jarvis.config import BrainConfig
from jarvis.tools.schema import to_json_schema


def _response(candidate: dict) -> Mock:
    response = Mock()
    response.ok = True
    response.json.return_value = {"candidates": [candidate]}
    return response


class GeminiActionRecoveryTests(unittest.TestCase):
    def _brain(self) -> GeminiVertexBrain:
        brain = GeminiVertexBrain(
            BrainConfig(model="gemini-3.6-flash", location="global")
        )
        brain._get_access_token_and_project = Mock(
            return_value=("access-token", "project-id")
        )
        return brain

    @patch.object(GeminiVertexBrain, "_http_post")
    def test_malformed_action_retries_with_forced_valid_function(self, post):
        malformed = _response({
            "content": {"parts": []},
            "finishReason": "MALFORMED_FUNCTION_CALL",
            "finishMessage": "Malformed function call: read_file(path=...)",
        })
        recovered = _response({
            "content": {"parts": [{
                "functionCall": {
                    "name": "read_file",
                    "args": {"path": "C:/project/app.py"},
                }
            }]},
            "finishReason": "STOP",
        })
        post.side_effect = [malformed, recovered]

        raw = self._brain().complete(
            'Return {"action": ...}.\nAvailable actions:\n'
            "  read_file(path)                    Read a file\n"
            "  finish(summary)                    Finish",
            [{"role": "user", "content": "Inspect the project."}],
        )

        self.assertEqual(json.loads(raw), {
            "thought": "",
            "action": "read_file",
            "args": {"path": "C:/project/app.py"},
        })
        self.assertEqual(post.call_count, 2)
        first_payload = post.call_args_list[0].kwargs["json"]
        recovery_payload = post.call_args_list[1].kwargs["json"]
        self.assertNotIn("tools", first_payload)
        self.assertEqual(
            recovery_payload["toolConfig"]["functionCallingConfig"]["mode"],
            "ANY",
        )
        names = {
            item["name"]
            for item in recovery_payload["tools"][0]["functionDeclarations"]
        }
        self.assertEqual(names, {"read_file", "finish"})
        self.assertEqual(
            recovery_payload["generationConfig"]["temperature"], 0.0
        )
        self.assertEqual(
            recovery_payload["generationConfig"]["responseMimeType"],
            "text/plain",
        )

    @patch.object(GeminiVertexBrain, "_http_post")
    def test_normal_json_response_does_not_enable_native_tools(self, post):
        post.return_value = _response({
            "content": {"parts": [{
                "text": '{"thought":"done","action":"finish",'
                        '"args":{"summary":"ok"}}'
            }]},
            "finishReason": "STOP",
        })

        raw = self._brain().complete(
            "Available actions:\n  finish(summary)  Finish",
            [{"role": "user", "content": "Done?"}],
        )

        self.assertEqual(json.loads(raw)["action"], "finish")
        self.assertNotIn("tools", post.call_args.kwargs["json"])

    @patch.object(GeminiVertexBrain, "_http_post")
    def test_persistent_malformed_error_keeps_vertex_detail(self, post):
        post.return_value = _response({
            "content": {"parts": []},
            "finishReason": "MALFORMED_FUNCTION_CALL",
            "finishMessage": "Malformed function call: edit_file(bad quote)",
        })

        with self.assertRaisesRegex(
            BrainError, r"MALFORMED_FUNCTION_CALL.*edit_file"
        ):
            self._brain().complete(
                "Available actions:\n  edit_file(path)  Edit",
                [{"role": "user", "content": "Make a change."}],
            )
        self.assertEqual(post.call_count, 3)


class GeminiFunctionSchemaTests(unittest.TestCase):
    def test_every_array_parameter_declares_its_items(self):
        declarations = to_json_schema()
        arrays = [
            prop
            for declaration in declarations
            for prop in declaration["parameters"]["properties"].values()
            if prop["type"] == "array"
        ]

        self.assertTrue(arrays)
        self.assertTrue(all("items" in prop for prop in arrays))

    def test_write_files_declares_path_and_content_objects(self):
        declarations = {
            declaration["name"]: declaration
            for declaration in to_json_schema()
        }
        files = declarations["write_files"]["parameters"]["properties"]["files"]

        self.assertEqual(files["items"]["type"], "object")
        self.assertEqual(
            set(files["items"]["properties"]), {"path", "content"}
        )
        self.assertEqual(
            set(files["items"]["required"]), {"path", "content"}
        )


if __name__ == "__main__":
    unittest.main()
