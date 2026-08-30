"""eval 模型：EvalCase 及各子结构（对应设计文档 3.1 Case Schema）。

tool 级 case 用 tool+params 直跑；plan/e2e 级用 question/history 走 agent 端点（Phase 1）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FaultSpec(BaseModel):
    """健壮性 case 的故障注入说明（设计文档 3.5）。Phase 0 只用 edge 型。"""

    kind: str = "empty"  # empty | bad_param | bad_layer | invalid_key | data_anomaly
    technique: str = "edge"  # edge | env_override
    env_override: dict[str, str] | None = None
    via: dict | None = None
    expected_signal: list[str] = Field(default_factory=list)


class GroundTruth(BaseModel):
    """真值断言（平台真计算 → 可校验）。"""

    layer_count_range: list[int] | None = None  # [min, max] 生成图层要素数
    fields_present: list[str] | None = None
    bbox_inside: list[float] | None = None  # [w, s, e, n]
    values: dict = Field(default_factory=dict)  # output.<key> == value；支持 {"$tol": x} 容差
    crs: str | None = None


class Expected(BaseModel):
    """期望（plan/e2e 用；tool 级主要用 expected_outcome）。"""

    intent: str | None = None
    operation: str | None = None  # plan 级：期望 PlanResult.operation（允许集用 "|" 分隔）
    tool_seq: list[str] | None = None  # plan 级：plan[].tool 有序子序列
    params: dict = Field(default_factory=dict)  # plan 级：PlanResult 顶层字段（distance/targets/…）
    clarify: bool | None = None  # 门1：期望触发/不触发澄清
    missing_data: list[str] = Field(default_factory=list)  # 门2：期望 data_check.missing / unmet_targets 含这些项
    objects: list[dict] = Field(default_factory=list)  # plan：对象「解析到图层 or 标缺」OR 断言 [{layer, words}]
    # 要点列表；内层 list = OR 组（任一命中即过），用于合法的多语义结局（如「删除」vs「隐藏」）
    answer_contains: list[str | list[str]] | None = None  # e2e：final_answer 应包含的要点
    expected_outcome: str = "success"  # success | empty | clarify | refuse | degraded


class Rubric(BaseModel):
    relevance: str | None = None
    completeness: str | None = None
    style: str | None = None


class Safety(BaseModel):
    forbidden: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    id: str
    title: str = ""
    category: str = "analysis"
    tier: str = "tool"  # tool | plan | e2e
    difficulty: str = "normal"  # easy | normal | hard
    tags: list[str] = Field(default_factory=list)
    question: str = ""
    history: list[dict] = Field(default_factory=list)
    fault: FaultSpec | None = None
    expected: Expected = Field(default_factory=Expected)
    ground_truth: GroundTruth = Field(default_factory=GroundTruth)
    rubric: Rubric = Field(default_factory=Rubric)
    safety: Safety = Field(default_factory=Safety)
    # tool 级专用
    tool: str | None = None
    params: dict = Field(default_factory=dict)
    # 外部依赖标注："" | "amap" | "mapbox" | "network"
    depends_external: str = ""
    # 可选夹具：执行前写入工作区的文件（自包含评估集）
    setup: list[dict] = Field(default_factory=list)  # [{path, content}]

    def validate_consistency(self) -> list[str]:
        """loader 级校验（设计文档 3.5）：fault 与 expected_outcome: success 互斥等。"""
        errors: list[str] = []
        if self.fault is not None and self.expected.expected_outcome == "success":
            errors.append(f"case {self.id}: fault 非空时 expected_outcome 不能是 success")
        if self.tier == "tool" and not self.tool:
            errors.append(f"case {self.id}: tier=tool 必须给 tool 字段")
        if self.tier in ("plan", "e2e") and not self.question:
            errors.append(f"case {self.id}: tier={self.tier} 必须给 question")
        return errors
