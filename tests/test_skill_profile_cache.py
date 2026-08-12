"""Unit tests for skill profile render caching and cache invalidation."""

from unittest.mock import AsyncMock

import pytest

from app.core.cache import cache_key
from app.models.skill_profile import SkillProfileEntry
from app.schemas.skill_profile import (
    ProficiencyLevel,
    ProfileDelta,
    SkillAssessment,
    SkillCategory,
)
from app.services.skill_profile import SkillProfileService


def _entry(skill: str) -> SkillProfileEntry:
    return SkillProfileEntry(
        user_id=1,
        skill=skill,
        skill_key=skill.lower(),
        category=SkillCategory.LANGUAGE,
        proficiency=ProficiencyLevel.INTERMEDIATE,
        detail="comfortable with async/await",
    )


@pytest.mark.asyncio
async def test_render_for_prompt_async_caches_successful_render(
    mock_cache: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.skill_profile.cache_service", mock_cache)
    svc = SkillProfileService()
    monkeypatch.setattr(svc, "load_entries", lambda user_id: [_entry("python")])

    result = await svc.render_for_prompt_async(1)
    assert "python" in result
    mock_cache.get.assert_called_once()
    mock_cache.set.assert_called_once()

    mock_cache.get.return_value = "cached profile"
    result2 = await svc.render_for_prompt_async(1)
    assert result2 == "cached profile"
    assert mock_cache.set.call_count == 1


@pytest.mark.asyncio
async def test_invalidate_cache_clears_cached_render(
    mock_cache: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.skill_profile.cache_service", mock_cache)
    svc = SkillProfileService()
    entries = [_entry("python")]
    monkeypatch.setattr(svc, "load_entries", lambda user_id: entries)

    first = await svc.render_for_prompt_async(1)
    assert "python" in first
    key = mock_cache.set.call_args[0][0]

    entries[:] = [_entry("fastapi")]
    mock_cache.get.return_value = None
    await svc._invalidate_cache(1)
    mock_cache.delete.assert_called_once_with(key)

    second = await svc.render_for_prompt_async(1)
    assert "fastapi" in second
    assert "python" not in second


@pytest.mark.asyncio
async def test_update_invalidates_cache_after_curate(
    mock_cache: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.skill_profile.cache_service", mock_cache)
    svc = SkillProfileService()
    monkeypatch.setattr(svc, "load_entries", lambda user_id: [_entry("python")])
    monkeypatch.setattr(svc, "curate", lambda user_id, delta: None)
    delta = ProfileDelta(
        upsert=[
            SkillAssessment(
                skill="fastapi",
                category=SkillCategory.FRAMEWORK,
                detail="async endpoints",
            )
        ]
    )
    monkeypatch.setattr(svc, "propose_delta", AsyncMock(return_value=delta))

    result = await svc.update(1, "we built a fastapi service")
    assert "python" in result
    mock_cache.delete.assert_called_once_with(cache_key("skill_profile", "1"))
