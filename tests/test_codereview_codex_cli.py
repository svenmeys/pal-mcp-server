import json
from pathlib import Path

import pytest

from clink.agents import AgentOutput
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers.base import ParsedCLIResponse
from tools.codereview import CodeReviewTool


@pytest.mark.asyncio
async def test_codereview_routes_expert_analysis_through_codex_cli(monkeypatch, tmp_path):
    tool = CodeReviewTool()
    reviewed_file = tmp_path / "example.py"
    reviewed_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    prompt_path = Path("systemprompts/clink/codex_codereviewer.txt").resolve()
    role = ResolvedCLIRole(name="codereviewer", prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="codex",
        executable=["codex"],
        working_dir=None,
        internal_args=["exec"],
        config_args=["--json"],
        env={},
        timeout_seconds=30,
        parser="codex_jsonl",
        runner="codex",
        roles={"codereviewer": role},
    )

    class DummyRegistry:
        def get_client(self, cli_name: str):
            assert cli_name == "codex"
            return client

    captured = {}

    class DummyAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentOutput(
                parsed=ParsedCLIResponse(content="Codex CLI review summary", metadata={"model_used": "gpt-5.4"}),
                sanitized_command=["codex", "exec", "--json"],
                returncode=0,
                stdout="{}",
                stderr="",
                duration_seconds=0.2,
                parser_name="codex_jsonl",
                output_file_content=None,
            )

    monkeypatch.setattr("clink.get_registry", lambda: DummyRegistry())
    monkeypatch.setattr("clink.agents.create_agent", lambda resolved_client: DummyAgent())

    result = await tool.execute_workflow(
        {
            "model": "codex",
            "step": "Perform the final review pass.",
            "step_number": 1,
            "total_steps": 1,
            "next_step_required": False,
            "findings": "Initial analysis found a simple arithmetic helper.",
            "relevant_files": [str(reviewed_file)],
            "files_checked": [str(reviewed_file)],
            "_resolved_model_name": "codex",
            "_workflow_provider_name": "codex",
        }
    )

    assert len(result) == 1
    payload = json.loads(result[0].text)

    assert payload["status"] == "calling_expert_analysis"
    assert payload["expert_analysis"]["status"] == "analysis_complete"
    assert payload["expert_analysis"]["raw_analysis"] == "Codex CLI review summary"
    assert payload["metadata"]["provider_used"] == "codex"
    assert payload["metadata"]["model_used"] == "gpt-5.4"

    assert captured["role"].name == "codereviewer"
    assert "code reviewer" in captured["system_prompt"]
    assert "Initial analysis found a simple arithmetic helper." in captured["prompt"]
