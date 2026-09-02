"""综合分析入口聚焦验收：受控路由、板块结构、真实 Skill 与失败语义。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.agent.comprehensive_analysis import (
    ACTIVITY_MODULE,
    ACTIVITY_SKILL,
    COMMERCIAL_MODULE,
    COMMERCIAL_SKILL,
    COMPREHENSIVE_INTENT,
    DIAGNOSIS_MODULE,
    DIAGNOSIS_SKILL,
    POPULATION_MODULE,
    PUBLIC_SERVICE_MODULE,
    PUBLIC_SERVICE_SKILL,
    execute_analysis_route,
    route_analysis_request,
)
from app.agent.executor import execute
from app.agent.planner import plan_for
from app.models.agent import ExecuteRequest


failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    marker = "PASS" if condition else "FAIL"
    print(f"[{marker}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def request_from_plan(question: str) -> ExecuteRequest:
    plan = plan_for(question)
    return ExecuteRequest(
        intent_id=plan.intent_id,
        question=question,
        summary=plan.summary,
        operation=plan.operation,
        targets=plan.targets,
        required_data=plan.required_data,
        plan=plan.plan,
    )


# 1) 普通规划用户的五类问法，不要求说出 Skill 名称。
activity_plan = plan_for("帮我看看这个片区白天和晚上、工作日和周末的活动规律")
check("活动模糊问法", activity_plan.intent_id == ACTIVITY_SKILL, activity_plan.thinking)

commercial_plan = plan_for("这里的店铺哪里密，哪些地方比较集聚？")
check("商业模糊问法", commercial_plan.intent_id == COMMERCIAL_SKILL, commercial_plan.thinking)

joint_plan = plan_for("商业网点和夜间活动有没有错位，供需匹配吗？")
check("商业×活动综合问法", joint_plan.intent_id == DIAGNOSIS_SKILL, joint_plan.thinking)

overall_plan = plan_for("根据现有数据做一次项目全面现状分析")
overall_skill_ids = [step.detail or "" for step in overall_plan.plan if step.tool == "execute_skill_script"]
check("项目全面现状分析路由", overall_plan.intent_id == COMPREHENSIVE_INTENT)
check(
    "全面分析形成四个核心板块计划",
    len(overall_skill_ids) == 4
    and all(skill in " ".join(overall_skill_ids) for skill in (ACTIVITY_SKILL, COMMERCIAL_SKILL, DIAGNOSIS_SKILL, PUBLIC_SERVICE_SKILL)),
)

target_plan = plan_for("请根据当前已有数据，对项目各方面进行一次尽可能完整的现状分析。")
check("目标综合分析原句确定性路由", target_plan.intent_id == COMPREHENSIVE_INTENT)

population_plan = plan_for("请给这个片区做一份人口画像，看看年龄和收入结构")
check("人口画像降级路由", population_plan.intent_id == "population-profile-degraded")
check(
    "人口画像明确阻断并降级活动时序",
    any(step.status.value == "blocked" and "人口属性" in str(step.detail) for step in population_plan.plan)
    and any(ACTIVITY_SKILL in str(step.detail) for step in population_plan.plan),
)


# 2) 用小型假 runner 检查稳定结构与综合依赖门；它不替代后面的真实 Skill 验收。
fake_calls: list[str] = []


def fake_success_runner(skill_id: str, params: dict[str, Any], *, ctx: Any = None) -> dict[str, Any]:
    fake_calls.append(skill_id)
    layer_id = f"fake_{skill_id}"
    return {
        "success": True,
        "skill_id": skill_id,
        "run_id": f"skillrun_fake_{skill_id}",
        "output_layer_id": layer_id,
        "layers": [{"layer_id": layer_id, "feature_count": 1}],
        "output": {
            "success": True,
            "summary": f"{skill_id} 真结果占位契约",
            "layer_id": layer_id,
            "stats": {"grid_count": 1},
            "evidence": {"limitations": ["测试限制"]},
            "layers": [{"layer_id": layer_id, "feature_count": 1}],
        },
    }


overall_route = route_analysis_request("根据现有数据做综合现状分析")
assert overall_route is not None
structured = execute_analysis_route(overall_route, run_skill=fake_success_runner).structured_analysis
check("综合结果包含稳定顶层字段", structured is not None and structured.requested_modules == [ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE, PUBLIC_SERVICE_MODULE])
check("综合结果四个板块全部完成", structured is not None and structured.ready_modules == [ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE, PUBLIC_SERVICE_MODULE])
check(
    "每个板块含运行标识/层级/统计/证据/限制/图层",
    structured is not None
    and all(
        module.status == "completed"
        and module.skill_id
        and module.run_id
        and module.achieved_level in {"L2", "L3"}
        and module.stats
        and module.evidence
        and module.limitations
        and module.output_layer
        for module in structured.modules
    ),
)


blocked_calls: list[str] = []


def fake_missing_activity(skill_id: str, params: dict[str, Any], *, ctx: Any = None) -> dict[str, Any]:
    blocked_calls.append(skill_id)
    if skill_id == ACTIVITY_SKILL:
        return {
            "success": False,
            "skill_id": skill_id,
            "run_id": "skillrun_fake_missing_activity",
            "layers": [],
            "output": {
                "success": False,
                "summary": "缺少夜间字段",
                "stop_code": "STOP_INVALID_INPUT",
                "stats": {},
                "evidence": {},
                "layers": [],
            },
        }
    return fake_success_runner(skill_id, params, ctx=ctx)


partially_blocked = execute_analysis_route(overall_route, run_skill=fake_missing_activity).structured_analysis
check(
    "缺数据时单因素停止且综合诊断不运行",
    partially_blocked is not None
    and ACTIVITY_MODULE in partially_blocked.blocked_modules
    and DIAGNOSIS_MODULE in partially_blocked.blocked_modules
    and COMMERCIAL_MODULE in partially_blocked.ready_modules
    and DIAGNOSIS_SKILL not in blocked_calls,
)
check(
    "缺数据不生成假结果标识",
    partially_blocked is not None
    and all(module.output_layer is None for module in partially_blocked.modules if module.status == "blocked"),
)


# 3) 人口画像真实降级：活动 Skill 运行，人口板块保持 blocked。
population_result = execute(request_from_plan("请做人群画像，说明年龄收入和昼夜特征"))
population_analysis = population_result.structured_analysis
check(
    "人口画像只运行活动 Skill",
    population_analysis is not None
    and population_analysis.ready_modules == [ACTIVITY_MODULE]
    and POPULATION_MODULE in population_analysis.blocked_modules,
)
check(
    "人口降级保持语义边界",
    "活动强度不是人口数量或人口结构" in str(population_result.final_answer)
    and "人口属性数据" in str(population_result.final_answer),
)


# 4) 真实综合运行：必须分别产出四个 Skill 的 run、地图、统计和证据。
real_result = execute(request_from_plan("根据现有数据做一次项目全面现状分析"))
real_analysis = real_result.structured_analysis
check(
    "真实综合分析四个板块完成",
    real_analysis is not None
    and real_analysis.ready_modules == [ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE, PUBLIC_SERVICE_MODULE]
    and not real_analysis.blocked_modules,
)
check(
    "真实综合结果可追溯",
    real_analysis is not None
    and all(module.run_id and module.output_layer and module.stats and module.evidence for module in real_analysis.modules),
)
check(
    "综合结论包含三条规划语义红线和候选坐标措辞",
    all(
        text in str(real_result.final_answer)
        for text in (
            "活动强度不是人口数量或人口结构",
            "商业点数量/密度不是营业额、经营质量或消费等级",
            "空间共现不代表因果",
            "gcj02_to_wgs84 仍是候选坐标版本",
        )
    ),
)


# 5) 真实缺文件：Skill 自己校验失败，入口必须 blocked 且无输出图层。
activity_route = route_analysis_request("比较昼夜活动规律")
assert activity_route is not None
missing_result = execute_analysis_route(
    activity_route,
    params_by_skill={ACTIVITY_SKILL: {"vitality_path": "missing-activity.csv"}},
)
missing_analysis = missing_result.structured_analysis
check(
    "真实缺文件正确停止",
    missing_analysis is not None
    and missing_analysis.ready_modules == []
    and missing_analysis.blocked_modules == [ACTIVITY_MODULE]
    and missing_analysis.modules[0].stop_code == "STOP_INVALID_INPUT"
    and missing_analysis.modules[0].output_layer is None,
)


if failures:
    raise SystemExit(f"FAILED: {', '.join(failures)}")
print("ALL PASS")
