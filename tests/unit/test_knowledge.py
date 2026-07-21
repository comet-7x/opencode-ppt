"""知识点载入/选点/数据源单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.generator.common.errors import KnowledgeError
from app.services.generator.common.knowledge import (
    FileKnowledgeSource,
    all_points,
    load_knowledge,
    resolve_knowledge,
    select_points,
)


def test_load_knowledge_passes_dict_through(knowledge: dict) -> None:
    assert load_knowledge(knowledge) is knowledge


def test_load_knowledge_reads_file(tmp_path: Path, knowledge: dict) -> None:
    p = tmp_path / "k.json"
    p.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    assert load_knowledge(p)["course"]["title"] == "海洋管理概论"


def test_all_points_ignores_unknown_fields(knowledge: dict) -> None:
    """K002 里有个"多余字段"，_clean 应过滤掉而不是构造报错。"""
    pts = all_points(knowledge)
    assert [p.id for p in pts] == ["K001", "K002"]
    assert pts[1].name == "海洋资源管理"


def test_select_points_preserves_requested_order(knowledge: dict) -> None:
    assert [p.id for p in select_points(knowledge, ["K002", "K001"])] == ["K002", "K001"]


def test_select_points_ignores_unknown_and_empty(knowledge: dict) -> None:
    assert [p.id for p in select_points(knowledge, ["nope"])] == []
    assert select_points(knowledge, []) == []


def test_all_points_on_empty() -> None:
    assert all_points({"points": []}) == []
    assert all_points({}) == []


# ————————————————————— FileKnowledgeSource ————————————————————— #
def test_file_source_reads(tmp_path: Path, knowledge: dict) -> None:
    p = tmp_path / "k.json"
    p.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    assert FileKnowledgeSource().fetch(str(p))["course"]["title"] == "海洋管理概论"


def test_file_source_missing_raises_knowledge_error() -> None:
    with pytest.raises(KnowledgeError, match="不存在"):
        FileKnowledgeSource().fetch("/no/such/file.json")


def test_file_source_bad_json_raises_knowledge_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="读取/解析失败"):
        FileKnowledgeSource().fetch(str(p))


# ————————————————————— resolve_knowledge ————————————————————— #
def test_resolve_prefers_inline(knowledge: dict) -> None:
    """inline 直通，不触碰 source（故传 None 也不该炸）。"""
    assert resolve_knowledge(None, inline=knowledge, ref="/ignored") is knowledge  # type: ignore[arg-type]


def test_resolve_uses_source_when_only_ref(tmp_path: Path, knowledge: dict) -> None:
    p = tmp_path / "k.json"
    p.write_text(json.dumps(knowledge, ensure_ascii=False), encoding="utf-8")
    got = resolve_knowledge(FileKnowledgeSource(), inline=None, ref=str(p))
    assert got["course"]["title"] == "海洋管理概论"


def test_resolve_without_any_source_raises() -> None:
    with pytest.raises(KnowledgeError, match="需提供"):
        resolve_knowledge(FileKnowledgeSource(), inline=None, ref=None)
