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
            BrainConfig(model="gemini-3.8-flash", location="global")
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

class GeminiRoleAlternationAndAnswerTests(unittest.TestCase):
    def _brain(self) -> GeminiVertexBrain:
        brain = GeminiVertexBrain(
            BrainConfig(model="gemini-3.8-flash", location="global")
        )
        brain._get_access_token_and_project = Mock(
            return_value=("access-token", "project-id")
        )
        return brain

    @patch.object(GeminiVertexBrain, "_http_post")
    def test_gemini_vertex_brain_strictly_alternates_roles_on_consecutive_user_messages(self, post):
        brain = self._brain()
        response = _response({
            "content": {"parts": [{"text": json.dumps({"action": "finish", "args": {"summary": "done"}})}]},
            "finishReason": "STOP",
        })
        post.return_value = response

        messages = [
            {"role": "user", "content": "TASK: organize files"},
            {"role": "assistant", "content": json.dumps({"action": "ask", "args": {"question": "Which folder?"}})},
            {"role": "user", "content": "RESULT: the user answered: Downloads"},
            {"role": "user", "content": "ACTIVE WINDOW: Explorer"},
        ]

        brain.complete("You are Jarvis.", messages)

        self.assertTrue(post.called)
        payload = post.call_args[1]["json"]
        contents = payload["contents"]

        # Ensure strict alternation: user -> model -> user
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[2]["role"], "user")

        # The consecutive user turns must be merged into the 3rd turn
        combined_text = "".join(p["text"] for p in contents[2]["parts"])
        self.assertIn("Downloads", combined_text)
        self.assertIn("Explorer", combined_text)

    def test_with_observation_appends_to_latest_user_turn(self):
        from jarvis.agent.loop import Agent
        from jarvis.config import Config
        from jarvis.perception.elements import Observation

        agent = Agent(self._brain(), Config())
        obs = Observation(elements=[], screen_size=(1920, 1080), active_window="Notepad")

        messages = [
            {"role": "user", "content": "TASK: do work"},
            {"role": "assistant", "content": "thinking"},
            {"role": "user", "content": "RESULT: user answered: Proceed"},
        ]

        out = agent._with_observation(messages, obs)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1]["role"], "user")
        self.assertIn("RESULT: user answered: Proceed", out[-1]["content"])
        self.assertIn("ACTIVE WINDOW: Notepad", out[-1]["content"])

    def test_telemetry_tracker_updates_on_answer_received(self):
        from jarvis.live.telemetry_state import TaskTelemetryTracker

        tracker = TaskTelemetryTracker()
        tracker.reset_for_new_task("test task")

        tracker.update_event({"event": "ask", "question": "Which path?"})
        self.assertEqual(tracker.status, "waiting_user")
        self.assertEqual(tracker.pending_question, "Which path?")

        tracker.update_event({"event": "answer_received", "question": "Which path?", "answer": "C:/data"})
        self.assertEqual(tracker.status, "running")
        self.assertEqual(tracker.pending_question, "")
        self.assertIn("C:/data", tracker.last_result_summary)

    def test_supervisor_handles_answer_agent_question_tool_call(self):
        from jarvis.config import Config
        from jarvis.live.supervisor import LiveVoiceSupervisor
        supervisor = LiveVoiceSupervisor(Config(), agent=Mock())

        supervisor._waiting_for_answer = True
        res = supervisor._on_live_tool_call("answer_agent_question", {"answer": "Desktop"}, "call_123")
        self.assertEqual(res, {"status": "answered", "answer": "Desktop"})
        self.assertFalse(supervisor._waiting_for_answer)
        self.assertEqual(supervisor._question_answer_queue.get_nowait(), "Desktop")


if __name__ == "__main__":
    unittest.main()
