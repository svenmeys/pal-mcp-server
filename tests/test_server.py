"""
Tests for the main server functionality
"""

import json

import pytest
from mcp.types import TextContent

import server
from server import handle_call_tool


class TestServerTools:
    """Test server tool handling"""

    @pytest.mark.asyncio
    async def test_handle_call_tool_unknown(self):
        """Test calling an unknown tool"""
        result = await handle_call_tool("unknown_tool", {})
        assert len(result) == 1
        assert "Unknown tool: unknown_tool" in result[0].text

    @pytest.mark.asyncio
    async def test_handle_chat(self):
        """Test chat functionality using real integration testing"""
        import importlib
        import os

        # Set test environment
        os.environ["PYTEST_CURRENT_TEST"] = "test"

        # Save original environment
        original_env = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "DEFAULT_MODEL": os.environ.get("DEFAULT_MODEL"),
        }

        try:
            # Set up environment for real provider resolution
            os.environ["OPENAI_API_KEY"] = "sk-test-key-server-chat-test-not-real"
            os.environ["DEFAULT_MODEL"] = "o3-mini"

            # Clear other provider keys to isolate to OpenAI
            for key in ["GEMINI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY"]:
                os.environ.pop(key, None)

            # Reload config and clear registry
            import config

            importlib.reload(config)
            from providers.registry import ModelProviderRegistry

            ModelProviderRegistry._instance = None

            # Test with real provider resolution
            try:
                result = await handle_call_tool("chat", {"prompt": "Hello Gemini", "model": "o3-mini"})

                # If we get here, check the response format
                assert len(result) == 1
                # Parse JSON response
                import json

                response_data = json.loads(result[0].text)
                assert "status" in response_data

            except Exception as e:
                # Expected: API call will fail with fake key
                error_msg = str(e)
                # Should NOT be a mock-related error
                assert "MagicMock" not in error_msg
                assert "'<' not supported between instances" not in error_msg

                # Should be a real provider error
                assert any(
                    phrase in error_msg
                    for phrase in ["API", "key", "authentication", "provider", "network", "connection"]
                )

        finally:
            # Restore environment
            for key, value in original_env.items():
                if value is not None:
                    os.environ[key] = value
                else:
                    os.environ.pop(key, None)

            # Reload config and clear registry
            importlib.reload(config)
            ModelProviderRegistry._instance = None

    @pytest.mark.asyncio
    async def test_handle_version(self):
        """Test getting version info"""
        result = await handle_call_tool("version", {})
        assert len(result) == 1

        response = result[0].text
        # Parse the JSON response
        import json

        data = json.loads(response)
        assert data["status"] == "success"
        content = data["content"]

        # Check for expected content in the markdown output
        assert "# PAL MCP Server Version" in content
        assert "## Server Information" in content
        assert "## Configuration" in content
        assert "Current Version" in content

    @pytest.mark.asyncio
    async def test_handle_codereview_codex_uses_cli_path(self, monkeypatch):
        """codereview should allow model='codex' without provider registry validation."""

        captured_arguments = {}

        class DummyCodeReviewTool:
            def requires_model(self):
                return True

            def get_model_category(self):
                from tools.models import ToolModelCategory

                return ToolModelCategory.EXTENDED_REASONING

            def get_expert_analysis_cli_name(self, request):
                # Marks this as a CLI-routing-capable (workflow) tool so the
                # server boundary applies the CLI bypass.
                return "codex"

            async def execute(self, arguments):
                captured_arguments.update(arguments)
                return [TextContent(type="text", text=json.dumps({"status": "success"}))]

        monkeypatch.setitem(server.TOOLS, "codereview", DummyCodeReviewTool())
        monkeypatch.setattr(
            "providers.registry.ModelProviderRegistry.get_provider_for_model",
            lambda model_name: None,
        )

        result = await handle_call_tool(
            "codereview",
            {
                "model": "codex",
                "step": "Review the changes",
                "step_number": 1,
                "total_steps": 1,
                "next_step_required": False,
                "findings": "Initial findings",
                "relevant_files": ["/tmp/example.py"],
            },
        )

        assert len(result) == 1
        assert captured_arguments["_resolved_model_name"] == "codex"
        assert captured_arguments["_workflow_provider_name"] == "codex"
        assert "_model_context" not in captured_arguments
