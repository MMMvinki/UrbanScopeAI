"""项目模型：项目信息 + 项目操作请求。"""

from __future__ import annotations

import re
from pydantic import BaseModel, Field


def new_project_id(name: str) -> str:
    """项目 id：由名称拼音/字符净化而来，转小写、去空白，保留字母数字下划线。"""
    stem = re.sub(r"[^a-zA-Z0-9_一-龥]+", "_", str(name or "project")).strip("_")
    if not stem:
        stem = "project"
    return f"proj_{stem}"[:40]


class ProjectInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str = ""
    layer_count: int = 0
    run_count: int = 0
    pinned_at: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="项目名称")
    description: str = Field(default="", max_length=300, description="项目描述")


class ProjectUpdate(BaseModel):
    """项目更新：改名 / 改描述 / 置顶（均可选，只更新传了的字段）。"""

    name: str | None = Field(default=None, max_length=60, description="新名称")
    description: str | None = Field(default=None, max_length=300, description="新描述")
    pinned: bool | None = Field(default=None, description="是否置顶（null=不改）")


class ProjectDetail(ProjectInfo):
    """项目详情：信息 + 数据文件清单 + 运行历史摘要。"""

    files: list[dict] = Field(default_factory=list)  # [{name, type, size, on_map}]
    runs: list[dict] = Field(default_factory=list)  # [{run_id, question, created_at, tool_count}]
