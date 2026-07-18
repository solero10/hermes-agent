"""Per-job cron reasoning effort overrides."""

import inspect
import json
from typing import Any, cast

import pytest

from cron.jobs import create_job, update_job
from cron.scheduler import _merge_mcp_into_per_job_toolsets, _resolve_job_reasoning_config
from tools.cronjob_tools import CRONJOB_SCHEMA, cronjob


@pytest.fixture
def isolated_cron(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")


def test_job_reasoning_effort_overrides_global_config():
    assert _resolve_job_reasoning_config(
        {"reasoning_effort": "low"}, {"agent": {"reasoning_effort": "high"}}
    ) == {"enabled": True, "effort": "low"}


def test_job_without_override_inherits_global_config():
    assert _resolve_job_reasoning_config(
        {}, {"agent": {"reasoning_effort": "high"}}
    ) == {"enabled": True, "effort": "high"}


def test_empty_or_malformed_override_falls_back_to_global():
    global_cfg = {"agent": {"reasoning_effort": "high"}}
    assert _resolve_job_reasoning_config({"reasoning_effort": ""}, global_cfg) == {
        "enabled": True, "effort": "high"
    }
    assert _resolve_job_reasoning_config({"reasoning_effort": "extreme"}, global_cfg) == {
        "enabled": True, "effort": "high"
    }
    assert _resolve_job_reasoning_config({"reasoning_effort": "low"}, {"agent": "bad"}) == {
        "enabled": True, "effort": "low"
    }


def test_create_and_update_validate_reasoning_effort(isolated_cron):
    job = create_job("review", "every 1h", reasoning_effort="LOW")
    assert job["reasoning_effort"] == "low"
    assert update_job(job["id"], {"reasoning_effort": "minimal"})["reasoning_effort"] == "minimal"
    with pytest.raises(ValueError, match="reasoning_effort"):
        update_job(job["id"], {"reasoning_effort": "extreme"})
    with pytest.raises(ValueError, match="reasoning_effort"):
        create_job("review", "every 1h", reasoning_effort=cast(Any, 123))


def test_cronjob_tool_can_set_and_clear_reasoning_effort(isolated_cron):
    prop = CRONJOB_SCHEMA["parameters"]["properties"]["reasoning_effort"]
    assert "" in prop["enum"]
    result = json.loads(cronjob(
        action="create", prompt="review", schedule="every 1h", reasoning_effort="low"
    ))
    assert result["success"] is True
    assert result["job"]["reasoning_effort"] == "low"

    cleared = json.loads(cronjob(
        action="update", job_id=result["job_id"], reasoning_effort=""
    ))
    assert cleared["success"] is True
    assert cleared["job"]["reasoning_effort"] is None


def test_new_parameter_is_appended_for_positional_compatibility():
    create_params = list(inspect.signature(create_job).parameters)
    tool_params = list(inspect.signature(cronjob).parameters)
    assert create_params[-1] == "reasoning_effort"
    assert tool_params[-1] == "reasoning_effort"


def test_no_mcp_sentinel_enforces_native_file_only_tools():
    assert _merge_mcp_into_per_job_toolsets(
        ["file", "no_mcp"],
        {"mcp_servers": {"cortexdb": {"enabled": True}}},
    ) == ["file"]
