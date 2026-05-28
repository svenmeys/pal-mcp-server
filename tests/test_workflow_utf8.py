"""
Unit tests to validate UTF-8 encoding in workflow tools
and the generation of properly encoded JSON responses.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from clink.agents import AgentOutput
from clink.models import ResolvedCLIClient, ResolvedCLIRole
from clink.parsers.base import ParsedCLIResponse
from tools.analyze import AnalyzeTool
from tools.codereview import CodeReviewTool
from tools.debug import DebugIssueTool


def _make_cli_mocks(content: str, captured: dict, *, role_name: str = "default"):
    """Build fake clink registry + agent so expert analysis returns ``content`` hermetically.

    Expert analysis now routes through a clink CLI instead of a provider's
    generate_content. These return a fake registry getter and create_agent that
    yield the given content without spawning a real CLI subprocess. ``captured``
    records the kwargs passed to ``agent.run``.

    Returns (get_registry_fn, create_agent_fn) suitable for patching.
    """
    prompt_path = Path("systemprompts/clink/codex_codereviewer.txt").resolve()
    role = ResolvedCLIRole(name=role_name, prompt_path=prompt_path, role_args=[])
    client = ResolvedCLIClient(
        name="claude",
        executable=["claude"],
        working_dir=None,
        internal_args=[],
        config_args=[],
        env={},
        timeout_seconds=30,
        parser="codex_jsonl",
        runner="codex",
        roles={role_name: role},
    )

    class DummyRegistry:
        def get_client(self, cli_name: str):
            return client

        def list_clients(self):
            return ["claude", "codex", "gemini"]

    class DummyAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentOutput(
                parsed=ParsedCLIResponse(content=content, metadata={"model_used": "test-model"}),
                sanitized_command=["claude"],
                returncode=0,
                stdout="{}",
                stderr="",
                duration_seconds=0.1,
                parser_name="codex_jsonl",
                output_file_content=None,
            )

    return (lambda: DummyRegistry()), (lambda resolved_client: DummyAgent())


class TestWorkflowToolsUTF8(unittest.IsolatedAsyncioTestCase):
    """Tests for UTF-8 encoding in workflow tools."""

    def setUp(self):
        """Test setup."""
        self.original_locale = os.getenv("LOCALE")
        # Default to French for tests
        os.environ["LOCALE"] = "fr-FR"

    def tearDown(self):
        """Cleanup after tests."""
        if self.original_locale is not None:
            os.environ["LOCALE"] = self.original_locale
        else:
            os.environ.pop("LOCALE", None)

    def test_workflow_json_response_structure(self):
        """Test the structure of JSON responses from workflow tools."""
        # Mock response with UTF-8 characters
        test_response = {
            "status": "pause_for_analysis",
            "step_number": 1,
            "total_steps": 3,
            "next_step_required": True,
            "findings": "Code analysis reveals performance issues 🔍",
            "files_checked": ["/src/main.py"],
            "relevant_files": ["/src/main.py"],
            "issues_found": [{"severity": "high", "description": "Function too complex - refactoring needed"}],
            "investigation_required": True,
            "required_actions": ["Review code dependencies", "Analyze architectural patterns"],
        }

        # Test JSON serialization with ensure_ascii=False
        json_str = json.dumps(test_response, indent=2, ensure_ascii=False)

        # Check UTF-8 characters are preserved
        self.assertIn("🔍", json_str)
        # No escaped characters
        self.assertNotIn("\\u", json_str)

        # Test parsing
        parsed = json.loads(json_str)
        self.assertEqual(parsed["findings"], test_response["findings"])
        self.assertEqual(len(parsed["issues_found"]), 1)

    async def test_analyze_tool_utf8_response(self):
        """Test that the analyze tool returns correct UTF-8 responses.

        Expert analysis now routes through a clink CLI, so we mock the CLI agent
        and assert the (UTF-8) content flows through unchanged.
        """
        content = json.dumps(
            {
                "status": "analysis_complete",
                "raw_analysis": "Analysis completed successfully 🔍",
            },
            ensure_ascii=False,
        )
        captured = {}
        get_registry_fn, create_agent_fn = _make_cli_mocks(content, captured)

        with (
            patch("clink.get_registry", get_registry_fn),
            patch("clink.agents.create_agent", create_agent_fn),
        ):
            analyze_tool = AnalyzeTool()
            result = await analyze_tool.execute(
                {
                    "step": "Analyze system architecture to identify issues",
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "Starting architectural analysis of Python code",
                    "relevant_files": ["/test/main.py"],
                    "model": "flash",
                }
            )

        # Checks
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)

        # Parse the response - must be valid UTF-8 JSON
        response_text = result[0].text
        response_data = json.loads(response_text)

        # Structure checks
        self.assertIn("status", response_data)

        # The CLI agent should have been invoked for expert analysis
        self.assertTrue(captured, "Expected expert analysis to route through the CLI agent")
        # UTF-8 content preserved end to end
        self.assertIn("🔍", response_data["expert_analysis"]["raw_analysis"])

    async def test_codereview_tool_french_findings(self):
        """Test that the codereview tool produces findings in French."""
        content = json.dumps(
            {
                "status": "analysis_complete",
                "raw_analysis": """
🔴 CRITIQUE: Aucun problème critique trouvé.

🟠 ÉLEVÉ: Fichier example.py:42 - Fonction trop complexe
→ Problème: La fonction process_data() contient trop de responsabilités
→ Solution: Décomposer en fonctions plus petites et spécialisées

🟡 MOYEN: Gestion d'erreurs insuffisante
→ Problème: Plusieurs fonctions n'ont pas de gestion d'erreurs appropriée
→ Solution: Ajouter des try-catch et validation des paramètres

✅ Points positifs:
• Code bien commenté et lisible
• Nomenclature cohérente
• Tests unitaires présents
""",
            },
            ensure_ascii=False,
        )
        captured = {}
        get_registry_fn, create_agent_fn = _make_cli_mocks(content, captured, role_name="codereviewer")

        with (
            patch("clink.get_registry", get_registry_fn),
            patch("clink.agents.create_agent", create_agent_fn),
        ):
            codereview_tool = CodeReviewTool()
            result = await codereview_tool.execute(
                {
                    "step": "Complete review of Python code",
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "Code review complete",
                    "relevant_files": ["/test/example.py"],
                    "model": "codex",
                }
            )

        # Checks
        self.assertIsNotNone(result)
        response_text = result[0].text
        response_data = json.loads(response_text)

        # Expert analysis must be present and carry the French content
        self.assertIn("expert_analysis", response_data)
        analysis = response_data["expert_analysis"]["raw_analysis"]
        # Check for French characters
        self.assertIn("ÉLEVÉ", analysis)
        self.assertIn("problème", analysis)
        self.assertIn("spécialisées", analysis)
        self.assertIn("appropriée", analysis)
        self.assertIn("paramètres", analysis)
        self.assertIn("présents", analysis)
        # Check for emojis
        self.assertIn("🔴", analysis)
        self.assertIn("🟠", analysis)
        self.assertIn("🟡", analysis)
        self.assertIn("✅", analysis)

    async def test_debug_tool_french_error_analysis(self):
        """Test that the debug tool analyzes errors in French."""
        content = json.dumps(
            {
                "status": "analysis_complete",
                "raw_analysis": (
                    "L'erreur concerne la variable 'données' qui n'est pas définie. "
                    "Cause probable: import manquant."
                ),
            },
            ensure_ascii=False,
        )
        captured = {}
        get_registry_fn, create_agent_fn = _make_cli_mocks(content, captured)

        with (
            patch("clink.get_registry", get_registry_fn),
            patch("clink.agents.create_agent", create_agent_fn),
        ):
            debug_tool = DebugIssueTool()
            result = await debug_tool.execute(
                {
                    "step": "Analyze NameError in data processing file",
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "Error detected during script execution",
                    "files_checked": ["/src/data_processor.py"],
                    "relevant_files": ["/src/data_processor.py"],
                    "hypothesis": ("Variable 'données' not defined - missing import"),
                    "confidence": "medium",
                    "model": "test-model",
                }
            )

        # Checks
        self.assertIsNotNone(result)
        response_text = result[0].text
        response_data = json.loads(response_text)

        # Check response structure
        self.assertIn("status", response_data)

        # Check that UTF-8 characters are preserved end to end
        response_str = json.dumps(response_data, ensure_ascii=False)
        self.assertIn("données", response_str)

    def test_utf8_emoji_preservation_in_workflow_responses(self):
        """Test that emojis are preserved in workflow tool responses."""
        # Mock workflow response with various emojis
        test_data = {
            "status": "analysis_complete",
            "severity_indicators": {
                "critical": "🔴",
                "high": "🟠",
                "medium": "🟡",
                "low": "🟢",
                "success": "✅",
                "error": "❌",
                "warning": "⚠️",
            },
            "progress": "Analysis completed 🎉",
            "recommendations": [
                "Optimize performance 🚀",
                "Improve documentation 📚",
                "Add unit tests 🧪",
            ],
        }

        # Test JSON encoding with ensure_ascii=False
        json_str = json.dumps(test_data, ensure_ascii=False, indent=2)

        # Check emojis are preserved
        self.assertIn("🔴", json_str)
        self.assertIn("🟠", json_str)
        self.assertIn("🟡", json_str)
        self.assertIn("🟢", json_str)
        self.assertIn("✅", json_str)
        self.assertIn("❌", json_str)
        self.assertIn("⚠️", json_str)
        self.assertIn("🎉", json_str)
        self.assertIn("🚀", json_str)
        self.assertIn("📚", json_str)
        self.assertIn("🧪", json_str)

        # No escaped Unicode
        self.assertNotIn("\\u", json_str)

        # Test parsing preserves emojis
        parsed = json.loads(json_str)
        self.assertEqual(parsed["severity_indicators"]["critical"], "🔴")
        self.assertEqual(parsed["progress"], "Analysis completed 🎉")


if __name__ == "__main__":
    unittest.main(verbosity=2)
