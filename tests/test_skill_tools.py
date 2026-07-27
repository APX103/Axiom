"""Tests for skill-related builtin tools (search_skills, list_skills, skill)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from axiom_core.skills.catalog import SkillCatalog
from axiom_core.skills.parser import Skill
from axiom_core.tools.builtins.skills import list_skills


def _make_ctx(skills=None, loaded=None):
    catalog = SkillCatalog()
    for s in skills or []:
        catalog.add(s)
    return SimpleNamespace(skill_catalog=catalog, loaded_skills=set(loaded or []))


@pytest.mark.asyncio
async def test_list_skills_no_catalog():
    ctx = SimpleNamespace()
    result = await list_skills(ctx)
    assert "(skill catalog not configured)" in result


@pytest.mark.asyncio
async def test_list_skills_empty():
    ctx = _make_ctx()
    result = await list_skills(ctx)
    assert "(no skills available)" in result


@pytest.mark.asyncio
async def test_list_skills_groups_by_source():
    ctx = _make_ctx(
        [
            Skill(
                name="paper-writing", description="write papers", body="body", source="anthropic"
            ),
            Skill(name="my-skill", description="custom skill", body="body", source="custom"),
        ]
    )
    result = await list_skills(ctx)
    assert "Available skills (2):" in result
    assert "source: anthropic" in result
    assert "source: custom" in result
    assert "paper-writing" in result
    assert "my-skill" in result


@pytest.mark.asyncio
async def test_list_skills_loaded_marker():
    ctx = _make_ctx(
        [Skill(name="x", description="d", body="b", source="anthropic")],
        loaded=["x"],
    )
    result = await list_skills(ctx)
    assert "x [loaded]" in result


@pytest.mark.asyncio
async def test_list_skills_source_filter():
    ctx = _make_ctx(
        [
            Skill(name="a", description="builtin", body="b", source="anthropic"),
            Skill(name="b", description="custom", body="b", source="custom"),
        ]
    )
    result = await list_skills(ctx, source="custom")
    assert "b" in result
    assert "- a" not in result
    assert "source: anthropic" not in result

    missing = await list_skills(ctx, source="missing")
    assert "(no skills available for source 'missing')" in missing


@pytest.mark.asyncio
async def test_list_skills_include_body():
    ctx = _make_ctx(
        [Skill(name="x", description="d", body="first line\nsecond line", source="anthropic")]
    )
    result = await list_skills(ctx, include_body=True)
    assert "first line" in result
    assert "second line" in result
