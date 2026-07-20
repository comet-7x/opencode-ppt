"""知识点载入 + 选点 + 数据源抽象（三条产线共用）。

数据契约 `KnowledgePoint` 在 `models/generator`，这里只做「怎么拿到 / 怎么选」。

数据源抽象（会变的边界）：`KnowledgeSource.fetch(ref)` 按引用取一份 knowledge。
本版实现 `FileKnowledgeSource`（ref = 文件路径）；将来加 `DBKnowledgeSource` /
`CloudKnowledgeSource`（ref = db id / 云 key）不动上层。inline 传入的 dict 直通，
不走 source。logic 节点永远只吃解析后的 dict / KnowledgePoint 列表。
"""

from __future__ import annotations

import json
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.models.generator.knowledge import KnowledgePoint
from app.services.generator.common.errors import KnowledgeError


# ————————————————————— 载入 / 选点 ————————————————————— #
def _clean(p: dict[str, Any]) -> dict[str, Any]:
    """只保留 KnowledgePoint 认识的字段，忽略多余键。"""
    allowed = {f.name for f in dc_fields(KnowledgePoint)}
    return {k: v for k, v in p.items() if k in allowed}


def load_knowledge(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """载入 knowledge（路径或已解析 dict），返回 {course, points}。"""
    if isinstance(source, dict):
        return source
    return json.loads(Path(source).read_text(encoding="utf-8"))


def all_points(data: dict[str, Any]) -> list[KnowledgePoint]:
    return [KnowledgePoint(**_clean(p)) for p in data.get("points", [])]


def select_points(data: dict[str, Any], ids: list[str]) -> list[KnowledgePoint]:
    """按给定 ids 选点，保持 ids 传入顺序。"""
    order = {kid: i for i, kid in enumerate(ids)}
    points = [
        KnowledgePoint(**_clean(p))
        for p in data.get("points", [])
        if p["id"] in order
    ]
    points.sort(key=lambda p: order.get(p.id, 1 << 30))
    return points


# ————————————————————— 数据源抽象 ————————————————————— #
@runtime_checkable
class KnowledgeSource(Protocol):
    """按引用取一份 knowledge 的后端。实现：File(本版) / DB / Cloud(将来)。"""

    def fetch(self, ref: str) -> dict[str, Any]: ...


class FileKnowledgeSource:
    """ref = 服务端文件路径。"""

    def fetch(self, ref: str) -> dict[str, Any]:
        try:
            return json.loads(Path(ref).read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise KnowledgeError(f"knowledge 文件不存在：{ref}") from e
        except (json.JSONDecodeError, OSError) as e:
            raise KnowledgeError(f"knowledge 文件读取/解析失败：{ref}（{e}）") from e


def resolve_knowledge(
    source: KnowledgeSource, *, inline: dict[str, Any] | None, ref: str | None
) -> dict[str, Any]:
    """统一入口：inline 直通；否则经 source 按 ref 取。"""
    if inline is not None:
        return inline
    if ref:
        return source.fetch(ref)
    raise KnowledgeError("需提供 knowledge inline dict 或 ref")
