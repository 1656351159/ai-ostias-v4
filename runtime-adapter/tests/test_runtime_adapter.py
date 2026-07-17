from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import (
    SKILL_OUTPUT_FIELDS,
    validate_agent_result,
    validate_task,
)
from runtime_adapter import (
    BASELINE_TOOLS,
    OpenClawAdapterError,
    RuntimeAdapter,
    _skill_json_from_session,
    assess_tool_policy,
    sanitize,
    session_exec_commands,
    tool_policy_allows_exact,
    verify_db_job,
)


FIXTURE = ROOT / "fixtures" / "sample_crawl_task.json"


def rpc_wrapper(text="ok", error=False, session_id="session-1"):
    meta = {"agentMeta": {"sessionId": session_id}}
    if error:
        meta.update({"stopReason": "error", "completion": {"finishReason": "error"}})
    return json.dumps(
        {
            "runId": "run-1",
            "status": "ok",
            "summary": "completed",
            "result": {"payloads": [{"text": text}], "meta": meta},
        }
    )


def skill_payload(job_id="job-1", status="completed", error=None):
    return {
        "job_id": job_id,
        "status": status,
        "processed_pages": 2,
        "extracted_count": 1,
        "failed_subtasks": 0,
        "error": error,
    }


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["openclaw"], returncode, stdout=stdout, stderr=stderr)


class QueueAdapter(RuntimeAdapter):
    def __init__(self, outcomes, preflight=None, **kwargs):
        kwargs.setdefault("mode", "gateway")
        kwargs.setdefault("transport", "rpc")
        kwargs.setdefault("agent_id", "researcher-v4")
        kwargs.setdefault("timeout", 5)
        kwargs.setdefault("env", {})
        super().__init__(**kwargs)
        self.outcomes = list(outcomes)
        self.commands = []
        self.preflight_value = preflight

    def _run_cli(self, args, timeout=None):
        self.commands.append(list(args))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def preflight(self):
        if self.preflight_value is not None:
            self._last_preflight = self.preflight_value
            return self.preflight_value
        return super().preflight()


def strict_preflight(allowed=None):
    allowed = BASELINE_TOOLS if allowed is None else allowed
    return {
        "ok": True,
        "agent_id": "researcher-v4",
        "tool_policy": {
            "effective_allow": list(allowed),
            "dangerous_tools_exposed": [],
        },
        "checks": [],
    }


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_is_valid(self):
        validated = validate_task(self.task)
        self.assertEqual(validated["constraints"]["skill"], "crawl4more")

    def test_skill_field_is_required(self):
        del self.task["constraints"]["skill"]
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_unregistered_skill_is_rejected(self):
        self.task["constraints"]["skill"] = "not-a-skill"
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_crawl_skill_requires_network_opt_in(self):
        self.task["constraints"]["allow_network"] = False
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_network_flag_must_be_boolean(self):
        self.task["constraints"]["allow_network"] = "yes"
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_url_with_credentials_is_rejected(self):
        self.task["input"]["start_url"] = "https://user:pass@example.com"
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_non_http_url_is_rejected(self):
        self.task["input"]["start_url"] = "file:///etc/passwd"
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_extra_urls_are_validated(self):
        self.task["input"]["extra_urls"] = ["not-a-url"]
        with self.assertRaises(ValueError):
            validate_task(self.task)

    def test_output_schema_must_match_skill_contract(self):
        for field in SKILL_OUTPUT_FIELDS:
            task = json.loads(json.dumps(self.task))
            del task["output_schema"][field]
            with self.assertRaises(ValueError):
                validate_task(task)

    def test_numeric_bounds(self):
        task = json.loads(json.dumps(self.task))
        task["input"]["max_pages"] = 0
        with self.assertRaises(ValueError):
            validate_task(task)
        task = json.loads(json.dumps(self.task))
        task["input"]["slice_timeout"] = 1
        with self.assertRaises(ValueError):
            validate_task(task)


class AgentResultContractTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_valid_payload(self):
        normalized = validate_agent_result(skill_payload(), self.task)
        self.assertEqual(normalized["status"], "completed")
        self.assertEqual(normalized["processed_pages"], 2)

    def test_paused_is_a_valid_skill_status(self):
        normalized = validate_agent_result(skill_payload(status="paused"), self.task)
        self.assertEqual(normalized["status"], "paused")

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_agent_result(skill_payload(status="running"), self.task)

    def test_job_id_must_match_declared_job(self):
        self.task["input"]["job_id"] = "expected"
        with self.assertRaises(ValueError):
            validate_agent_result(skill_payload(job_id="other"), self.task)

    def test_counts_must_be_non_negative_ints(self):
        payload = skill_payload()
        payload["processed_pages"] = -1
        with self.assertRaises(ValueError):
            validate_agent_result(payload, self.task)
        payload = skill_payload()
        payload["extracted_count"] = True
        with self.assertRaises(ValueError):
            validate_agent_result(payload, self.task)


class SubmitBranchTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def submit_with_payload(self, payload_text, preflight=None):
        adapter = QueueAdapter(
            [completed(rpc_wrapper(payload_text, session_id="returned-session"))],
            preflight=preflight or strict_preflight(),
        )
        return adapter.submit(self.task), adapter

    def test_skill_success(self):
        result, adapter = self.submit_with_payload(json.dumps(skill_payload()))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.session_id, "returned-session")
        self.assertEqual(result.result["job_id"], "job-1")

    def test_skill_failure_maps_to_skill_execution_failed(self):
        payload = skill_payload(status="failed", error="boom")
        result, _ = self.submit_with_payload(json.dumps(payload))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "skill_execution_failed")

    def test_skill_paused_maps_to_skill_execution_failed(self):
        payload = skill_payload(status="paused", error="overall timeout")
        result, _ = self.submit_with_payload(json.dumps(payload))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "skill_execution_failed")

    def test_unparseable_reply_maps_to_invalid_agent_result(self):
        result, _ = self.submit_with_payload("no json here")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "invalid_agent_result")

    def test_fenced_json_reply_is_accepted(self):
        result, _ = self.submit_with_payload("```json\n%s\n```" % json.dumps(skill_payload()))
        self.assertEqual(result.status, "completed")

    def test_trailing_json_line_is_accepted(self):
        text = "working...\n%s" % json.dumps(skill_payload())
        result, _ = self.submit_with_payload(text)
        self.assertEqual(result.status, "completed")

    def test_unenforced_tool_policy_refuses_before_agent_call(self):
        preflight = strict_preflight()
        preflight["tool_policy"]["effective_allow"] = None
        adapter = QueueAdapter([], preflight=preflight)
        result = adapter.submit(self.task)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "tool_policy_unenforced")
        self.assertEqual(adapter.commands, [])

    def test_expanded_tool_request_refuses_before_agent_call(self):
        task = json.loads(json.dumps(self.task))
        task["constraints"]["allowed_tools"] = list(BASELINE_TOOLS) + ["browser"]
        adapter = QueueAdapter([], preflight=strict_preflight())
        result = adapter.submit(task)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "tool_policy_unenforced")
        self.assertEqual(adapter.commands, [])

    def test_db_status_mismatch_maps_to_skill_execution_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "crawler.db"
            connection = sqlite3.connect(str(db_path))
            connection.execute(
                "CREATE TABLE crawl_jobs (job_uuid TEXT, status TEXT, processed_pages INTEGER)"
            )
            connection.execute(
                "INSERT INTO crawl_jobs VALUES ('job-1', 'failed', 0)"
            )
            connection.commit()
            connection.close()
            task = json.loads(json.dumps(self.task))
            task["input"]["db_path"] = str(db_path)
            adapter = QueueAdapter(
                [completed(rpc_wrapper(json.dumps(skill_payload())))],
                preflight=strict_preflight(),
            )
            result = adapter.submit(task)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error.code, "skill_execution_failed")

    def test_db_consistency_passes_and_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "crawler.db"
            connection = sqlite3.connect(str(db_path))
            connection.execute(
                "CREATE TABLE crawl_jobs (job_uuid TEXT, status TEXT, processed_pages INTEGER)"
            )
            connection.execute(
                "INSERT INTO crawl_jobs VALUES ('job-1', 'completed', 2)"
            )
            connection.commit()
            connection.close()
            task = json.loads(json.dumps(self.task))
            task["input"]["db_path"] = str(db_path)
            adapter = QueueAdapter(
                [completed(rpc_wrapper(json.dumps(skill_payload())))],
                preflight=strict_preflight(),
            )
            result = adapter.submit(task)
            self.assertEqual(result.status, "completed")
            self.assertTrue(result.result["db_check"]["found"])


class TransportTests(unittest.TestCase):
    def test_rpc_command_shape_and_idempotency(self):
        adapter = QueueAdapter([completed(rpc_wrapper("ok"))])
        turn = adapter.invoke_text("hello", session_id="s-1", idempotency_key="submit:t-1")
        self.assertEqual(turn.session_id, "session-1")
        args = adapter.commands[0]
        self.assertEqual(args[:3], ["gateway", "call", "agent"])
        self.assertIn("--expect-final", args)
        params = json.loads(args[args.index("--params") + 1])
        self.assertEqual(params["message"], "hello")
        self.assertEqual(params["agentId"], "researcher-v4")
        self.assertEqual(params["sessionId"], "s-1")
        self.assertEqual(params["idempotencyKey"], "submit:t-1")

    def test_rpc_error_envelope_is_normalized(self):
        adapter = QueueAdapter(
            [completed(json.dumps({"ok": False, "error": {"code": "UNAVAILABLE", "message": "down"}}))]
        )
        with self.assertRaises(OpenClawAdapterError) as caught:
            adapter.invoke_text("hello")
        self.assertEqual(caught.exception.code, "openclaw_command_failed")

    def test_gateway_token_is_environment_only(self):
        observed = {}

        def fake_run(command, **kwargs):
            observed["command"] = command
            observed["env"] = kwargs["env"]
            return completed(rpc_wrapper("ok"))

        adapter = RuntimeAdapter(
            mode="gateway",
            transport="rpc",
            agent_id="researcher-v4",
            timeout=5,
            gateway_token="secret-sentinel",
            env={},
        )
        with mock.patch("runtime_adapter.subprocess.run", side_effect=fake_run):
            adapter.invoke_text("hello", session_id="session-safe")

        self.assertNotIn("secret-sentinel", " ".join(observed["command"]))
        self.assertEqual(observed["env"]["OPENCLAW_GATEWAY_TOKEN"], "secret-sentinel")

    def test_gateway_token_is_removed_from_cli_errors(self):
        adapter = QueueAdapter(
            [completed(stderr="authentication failed for secret-sentinel", returncode=1)],
            gateway_token="secret-sentinel",
        )
        with self.assertRaises(OpenClawAdapterError) as caught:
            adapter.invoke_text("hello")
        self.assertNotIn("secret-sentinel", caught.exception.message)

    def test_nested_runtime_error_is_not_false_success(self):
        adapter = QueueAdapter([completed(rpc_wrapper("LLM request failed.", error=True))])
        with self.assertRaises(OpenClawAdapterError) as caught:
            adapter.invoke_text("hello")
        self.assertEqual(caught.exception.code, "agent_execution_failed")

    def test_timeout_is_normalized(self):
        adapter = QueueAdapter([subprocess.TimeoutExpired(cmd=["openclaw"], timeout=1)])
        with self.assertRaises(OpenClawAdapterError) as caught:
            adapter.invoke_text("hello", timeout=1)
        self.assertEqual(caught.exception.code, "timeout")

    def test_invalid_cli_json_is_normalized(self):
        adapter = QueueAdapter([completed("not json")])
        with self.assertRaises(OpenClawAdapterError) as caught:
            adapter.invoke_text("hello")
        self.assertEqual(caught.exception.code, "invalid_cli_json")

    def test_local_mode_forces_agent_cli(self):
        adapter = RuntimeAdapter(mode="local", transport="rpc", agent_id="a", timeout=5, env={})
        self.assertEqual(adapter.transport, "agent-cli")


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.adapter = RuntimeAdapter(mode="gateway", agent_id="a", timeout=5, env={})

    def test_prompt_marks_task_as_untrusted(self):
        prompt = self.adapter._build_research_prompt(self.task)
        self.assertIn("untrusted data", prompt)

    def test_prompt_points_to_skill_and_entry(self):
        prompt = self.adapter._build_research_prompt(self.task)
        self.assertIn("crawl4more-skill/SKILL.md", prompt)
        self.assertIn("crawl4more-skill/run.py", prompt)
        self.assertIn("--start-url https://example.com", prompt)
        self.assertIn("--max-pages 2", prompt)

    def test_prompt_demands_last_line_json_echo(self):
        prompt = self.adapter._build_research_prompt(self.task)
        self.assertIn("LAST line of its stdout", prompt)
        self.assertIn("no markdown fences", prompt)

    def test_mock_crawler_prefixes_env(self):
        prompt = self.adapter._build_research_prompt(self.task)
        self.assertIn("USE_MOCK_CRAWLER=true", prompt)
        task = json.loads(json.dumps(self.task))
        task["input"]["mock_crawler"] = False
        prompt = self.adapter._build_research_prompt(task)
        self.assertNotIn("USE_MOCK_CRAWLER", prompt)

    def test_job_id_and_db_path_are_forwarded(self):
        task = json.loads(json.dumps(self.task))
        task["input"]["job_id"] = "j-9"
        task["input"]["db_path"] = "/tmp/x.db"
        prompt = self.adapter._build_research_prompt(task)
        self.assertIn("--job-id j-9", prompt)
        self.assertIn("--db-path /tmp/x.db", prompt)


class SessionAuditTests(unittest.TestCase):
    def write_session(self, records):
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".jsonl", delete=False
        )
        for record in records:
            handle.write(json.dumps(record) + "\n")
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_session_exec_commands(self):
        path = self.write_session(
            [
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "t1",
                                "name": "exec",
                                "arguments": {"command": "/usr/bin/python3 crawl4more-skill/run.py --start-url https://x"},
                            },
                            {"type": "toolCall", "id": "t2", "name": "read", "arguments": {}},
                        ],
                    },
                }
            ]
        )
        commands = session_exec_commands(path)
        self.assertEqual(len(commands), 1)
        self.assertIn("run.py", commands[0])

    def test_skill_json_fallback_reads_aggregated_output(self):
        summary = skill_payload()
        path = self.write_session(
            [
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "t1",
                                "name": "exec",
                                "arguments": {"command": "/usr/bin/python3 crawl4more-skill/run.py"},
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "t1",
                        "toolName": "exec",
                        "isError": False,
                        "content": [{"type": "text", "text": "..."}],
                        "details": {
                            "status": "completed",
                            "exitCode": 0,
                            "aggregated": "log line\n%s" % json.dumps(summary),
                        },
                    },
                },
            ]
        )
        value = _skill_json_from_session(path)
        self.assertIsNotNone(value)
        self.assertEqual(value["job_id"], "job-1")

    def test_skill_json_fallback_ignores_unrelated_exec(self):
        path = self.write_session(
            [
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "t9",
                                "name": "exec",
                                "arguments": {"command": "/usr/bin/python3 -c 'print(1)'"},
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "t9",
                        "toolName": "exec",
                        "isError": False,
                        "content": [],
                        "details": {"aggregated": json.dumps(skill_payload())},
                    },
                },
            ]
        )
        self.assertIsNone(_skill_json_from_session(path))


class PolicyTests(unittest.TestCase):
    def test_tool_policy_requires_an_exact_allowlist(self):
        coding = assess_tool_policy({"profile": "coding"})
        baseline = assess_tool_policy({"allow": list(BASELINE_TOOLS)})
        no_tools = assess_tool_policy({"allow": []})
        self.assertFalse(tool_policy_allows_exact(coding, []))
        self.assertTrue(tool_policy_allows_exact(baseline, BASELINE_TOOLS))
        self.assertTrue(tool_policy_allows_exact(no_tools, []))

    def test_exec_is_controlled_not_dangerous(self):
        policy = assess_tool_policy({"allow": list(BASELINE_TOOLS)})
        self.assertEqual(policy["dangerous_tools_exposed"], [])
        self.assertEqual(policy["controlled_tools_exposed"], ["exec"])
        self.assertEqual(policy["network_tools_exposed"], [])

    def test_write_and_browser_remain_dangerous(self):
        policy = assess_tool_policy({"allow": ["read", "exec", "write", "browser"]})
        # write 隐含 apply_patch（沿用 V3 归一化规则）
        self.assertEqual(policy["dangerous_tools_exposed"], ["apply_patch", "browser", "write"])

    def test_secret_values_are_redacted_recursively(self):
        sentinel = "never-log-this"
        value = sanitize(
            {
                "gateway_token": sentinel,
                "nested": {"password": sentinel},
                "url": "ws://localhost:1/?token=%s" % sentinel,
                "authorization": "Bearer %s" % sentinel,
            }
        )
        self.assertNotIn(sentinel, json.dumps(value))


class DbCheckTests(unittest.TestCase):
    def test_missing_database(self):
        report = verify_db_job("/nonexistent/none.db", "j")
        self.assertFalse(report["found"])
        self.assertIn("error", report)

    def test_found_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "c.db"
            connection = sqlite3.connect(str(db_path))
            connection.execute(
                "CREATE TABLE crawl_jobs (job_uuid TEXT, status TEXT, processed_pages INTEGER)"
            )
            connection.execute("INSERT INTO crawl_jobs VALUES ('j', 'completed', 3)")
            connection.commit()
            connection.close()
            report = verify_db_job(str(db_path), "j")
            self.assertTrue(report["found"])
            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["processed_pages"], 3)


if __name__ == "__main__":
    unittest.main()
