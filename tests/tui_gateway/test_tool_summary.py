import json

from tui_gateway.server import _tool_summary


def test_tool_summary_openbrain_search_found_count():
    result = json.dumps({
        "result": "Found 3 thought(s):\n\n--- Result 1 ---\n--- Result 2 ---\n--- Result 3 ---"
    })

    assert _tool_summary("mcp_cortexdb_search_thoughts", result, 6.4) == "3 found in 6.4s"


def test_tool_summary_openbrain_capture_is_privacy_safe():
    result = json.dumps({"success": True, "id": "thought-1"})

    assert _tool_summary("mcp_cortexdb_capture_thought", result, 1.2) == "saved in 1.2s"
