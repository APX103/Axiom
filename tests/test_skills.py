"""Skills 系统测试。

对照原版 0796.js (parser) + 0771.js (search) + 0799.js (catalog)。
测试: SKILL.md 解析 / tokenizer / BM25+Jaccard+RRF 检索 / catalog 扫描。
"""

from __future__ import annotations

import pytest

from operon.skills.catalog import (
    SkillCatalog,
    load_builtin_skills,
    load_claude_skills,
    load_custom_skills,
    load_project_skills,
)
from operon.skills.parser import Skill, parse_skill_md
from operon.skills.search import expand_query, search_skills, tokenize

# ---------- SKILL.md 解析 ----------


def test_parse_basic():
    content = """---
name: my-skill
description: Does useful things with data
---

# My Skill

When the user asks to process data, follow these steps.
"""
    s = parse_skill_md(content)
    assert s.name == "my-skill"
    assert "useful things" in s.description
    assert "My Skill" in s.body
    assert "process data" in s.body


def test_parse_missing_name_raises():
    with pytest.raises(ValueError, match="name"):
        parse_skill_md("---\ndescription: x\n---\nbody")


def test_parse_missing_description_raises():
    with pytest.raises(ValueError, match="description"):
        parse_skill_md("---\nname: x\n---\nbody")


def test_parse_invalid_name_raises():
    with pytest.raises(ValueError, match="invalid"):
        parse_skill_md("---\nname: Bad Name!\ndescription: x\n---\nbody")


def test_parse_description_truncated():
    long_desc = "x" * 2000
    s = parse_skill_md(f"---\nname: a\ndescription: {long_desc}\n---\nbody")
    assert len(s.description) == 1024


def test_parse_placeholder_substitution(tmp_path):
    content = "---\nname: x\ndescription: d\n---\nPath is {baseDir} and root {skillsRoot}"
    s = parse_skill_md(content, base_dir=tmp_path / "x")
    assert str(tmp_path / "x") in s.body
    assert str(tmp_path) in s.body


def test_metadata_tag():
    s = Skill(name="x", description="d", body="b", source="anthropic")
    tag = s.metadata_tag()
    assert '<skill-metadata' in tag
    assert 'name="x"' in tag
    assert 'source="anthropic"' in tag


def test_index_text_includes_body_slice():
    body = "word " * 1000
    s = parse_skill_md(f"---\nname: x\ndescription: d\n---\n{body}")
    assert len(s.index_text) <= 2000 + 50  # description + body slice


# ---------- tokenizer ----------


def test_tokenize_basic():
    tokens = tokenize("machine learning models")
    assert "machine" in tokens
    assert "learning" in tokens
    assert "model" in tokens  # 去复数


def test_tokenize_stopswords_filtered():
    tokens = tokenize("the use of a model")
    assert "the" not in tokens
    assert "use" not in tokens
    assert "model" in tokens


def test_tokenize_camel_case():
    tokens = tokenize("use OpenAlex search")
    assert "open" in tokens  # OpenAlex 拆开
    assert "alex" in tokens


def test_expand_query_abbrev():
    expanded = expand_query("use ml and pca")
    assert "machine learning" in expanded
    assert "principal component analysis" in expanded


# ---------- 检索 BM25+Jaccard+RRF ----------


@pytest.fixture
def sample_skills():
    return [
        Skill(
            name="paper-writer",
            description="write scientific papers manuscripts",
            body="academic writing",
        ),
        Skill(
            name="figure-maker",
            description="create plots charts figures matplotlib",
            body="visualization",
        ),
        Skill(name="data-cleaner", description="clean preprocess datasets", body="pandas numpy"),
        Skill(name="pdf-reader", description="extract text from pdf documents", body="pypdfium2"),
    ]


def test_search_relevant_result(sample_skills):
    results = search_skills(sample_skills, "write a research paper")
    assert len(results) > 0
    assert results[0].skill.name == "paper-writer"


def test_search_figure_match(sample_skills):
    results = search_skills(sample_skills, "create a chart plot")
    assert results[0].skill.name == "figure-maker"


def test_search_pdf_match(sample_skills):
    results = search_skills(sample_skills, "read pdf extract text")
    assert results[0].skill.name == "pdf-reader"


def test_search_no_match(sample_skills):
    results = search_skills(sample_skills, "xyzqwerty unrelated")
    assert results == []


def test_search_threshold_filters():
    """低分结果被 RRF_THRESHOLD=0.029 过滤。"""
    skills = [Skill(name="alpha", description="completely different topic", body="zzz")]
    results = search_skills(skills, "machine learning paper")
    # alpha 与 query 无关,应该被阈值过滤
    assert results == []


def test_search_max_results(sample_skills):
    results = search_skills(sample_skills, "data", max_results=2)
    assert len(results) <= 2


def test_search_score_above_threshold(sample_skills):
    results = search_skills(sample_skills, "paper")
    for r in results:
        assert r.score >= 0.029


# ---------- catalog ----------


def test_catalog_builtin_skills():
    skills = load_builtin_skills()
    names = [s.name for s in skills]
    assert "paper-narrative" in names
    assert "figure-composer" in names
    assert "pdf-explore" in names


def test_catalog_scan_disk(tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "my-tool").mkdir(parents=True)
    (skills_root / "my-tool" / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: a custom skill\n---\n# My Tool\ndo stuff",
        encoding="utf-8",
    )
    catalog = SkillCatalog(skills_root)
    assert catalog.get("my-tool") is not None
    assert "custom skill" in catalog.get("my-tool").description


def test_catalog_disabled(tmp_path):
    catalog = SkillCatalog()
    catalog.add(Skill(name="x", description="d", body="b"))
    catalog.set_disabled(["x"])
    assert catalog.get("x") is None


def test_catalog_fuzzy_suggest():
    catalog = SkillCatalog()
    catalog.add(Skill(name="paper-narrative", description="d", body="b"))
    catalog.add(Skill(name="figure-composer", description="d", body="b"))
    suggestions = catalog.fuzzy_suggest("paper-narrativ")  # 少一字母
    assert "paper-narrative" in suggestions


def test_catalog_get_nonexistent():
    catalog = SkillCatalog()
    assert catalog.get("nonexistent") is None


def test_load_project_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    ws = tmp_path / "ws"
    root = ws / ".axiom" / "skills"
    (root / "proj-skill").mkdir(parents=True)
    (root / "proj-skill" / "SKILL.md").write_text(
        "---\nname: proj-skill\ndescription: project skill\n---\nbody",
        encoding="utf-8",
    )
    skills = load_project_skills(ws)
    assert any(s.name == "proj-skill" and s.source == "project" for s in skills)


def test_load_claude_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = home / ".claude" / "skills"
    (root / "claude-skill").mkdir(parents=True)
    (root / "claude-skill" / "SKILL.md").write_text(
        "---\nname: claude-skill\ndescription: claude skill\n---\nbody",
        encoding="utf-8",
    )
    skills = load_claude_skills()
    assert any(s.name == "claude-skill" and s.source == "claude" for s in skills)


def test_load_custom_skills(tmp_path):
    root = tmp_path / "extra"
    (root / "custom-skill").mkdir(parents=True)
    (root / "custom-skill" / "SKILL.md").write_text(
        "---\nname: custom-skill\ndescription: custom skill\n---\nbody",
        encoding="utf-8",
    )
    skills = load_custom_skills([str(root)])
    assert any(s.name == "custom-skill" and s.source == "custom" for s in skills)


def test_multi_source_override_order():
    """后加载的来源覆盖先加载的同名 skill。"""
    catalog = SkillCatalog()
    catalog.add(Skill(name="same", description="builtin", body="b", source="anthropic"))
    catalog.add(Skill(name="same", description="global", body="g", source="global"))
    s = catalog.get("same")
    assert s is not None
    assert s.source == "global"
