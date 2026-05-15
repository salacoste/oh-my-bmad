"""Tests for _create_pr_draft edge cases — repo parsing, token guards (Story 5.14 review)."""

from __future__ import annotations

import pytest

from orchestrator_adapter.app.main import _create_pr_draft


def _make_settings(token: str = "ghp_test123") -> object:
    from orchestrator_adapter.app.config import OrchestratorSettings

    return OrchestratorSettings(github_token=token)


# --- repo parsing edge cases ---


@pytest.mark.asyncio
async def test_create_pr_draft_empty_repo() -> None:
    settings = _make_settings()
    result = await _create_pr_draft(settings, "T-001", "", "summary", "Title")
    assert result is None


@pytest.mark.asyncio
async def test_create_pr_draft_repo_no_slash() -> None:
    settings = _make_settings()
    result = await _create_pr_draft(settings, "T-001", "just-a-repo", "summary", "Title")
    assert result is None


@pytest.mark.asyncio
async def test_create_pr_draft_repo_trailing_slash() -> None:
    settings = _make_settings()
    result = await _create_pr_draft(settings, "T-001", "owner/", "summary", "Title")
    assert result is None


@pytest.mark.asyncio
async def test_create_pr_draft_repo_leading_slash() -> None:
    settings = _make_settings()
    result = await _create_pr_draft(settings, "T-001", "/repo", "summary", "Title")
    assert result is None


@pytest.mark.asyncio
async def test_create_pr_draft_no_token() -> None:
    settings = _make_settings(token="")
    result = await _create_pr_draft(settings, "T-001", "owner/repo", "summary", "Title")
    assert result is None
