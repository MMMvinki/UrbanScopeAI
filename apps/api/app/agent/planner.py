"""Planner：把任意规划问题转成可执行计划。

流程：
1. 问候/闲聊守卫（确定性，不调 LLM）——秒回、零成本；
2. 否则用 LLM Planner（DeepSeek）生成计划；LLM 不可用或失败时**不兜底**，
   直接如实告知失败与重试建议——规则引擎按词表查表产出的计划往往不是用户
   想要的（如「学校好远」会被猜成覆盖率分析），宁可坦白失败也不产出偏离意图的分析。
"""

from app.agent.llm_planner import try_plan
from app.agent.patterns import DEFAULT_DISTANCE
from app.models.agent import PlanResult


_CHAT_INTRO = (
    "你好！我是 UrbanScope 的空间分析助手，可以帮你在当前片区内做空间分析，例如：\n"
    "· 街区城市体检：学校、公园等设施的服务覆盖率\n"
    "· 15 分钟生活圈 / 公共服务设施评估\n"
    "· 人口密度与薄弱片区分布\n"
    "· 产业用地到轨道站点的通勤距离\n\n"
    "直接向我提问就行，比如「帮我对这个片区做一次街区城市体检」。"
)

# 混合句检测：句子里出现这些「实质请求」词，说明不只是闲聊，必须放行给 LLM，
# 不能被问候守卫吞掉（如「谢谢，顺便帮我看看学校覆盖率」）。
_ANALYSIS_HINTS = (
    "分析", "覆盖率", "覆盖", "距离", "密度", "叠加", "供需", "体检",
    "评估", "计算", "人口", "学校", "公园", "轨道", "地铁", "公交",
    "产业", "医院", "餐厅", "餐饮", "poi", "下载", "加载", "上传", "多少",
)


def _chat_plan(summary: str) -> PlanResult:
    """闲聊类问题：operation=chat，不触发任何空间分析。"""
    return PlanResult(
        intent_id="chat",
        summary=summary,
        operation="chat",
        targets=[],
        distance=DEFAULT_DISTANCE,
        target_distances={},
        required_data=[],
        plan=[],
        data_check=None,
    )


def _chat_reply_for(question: str) -> PlanResult | None:
    """确定性问候守卫：命中问候/致谢/告别/询问能力 → 直接返回文字回复，不调用 LLM。

    只处理「整句就是闲聊」的请求：句子里夹带实质分析意图的混合句不在此列，
    必须放行给 LLM，否则「谢谢，顺便看看学校覆盖率」会被吞掉真实请求。
    """
    q = question.strip().lower()
    if any(k in q for k in _ANALYSIS_HINTS):
        return None
    if any(k in q for k in ("你好", "您好", "嗨", "哈喽", "早上好", "中午好", "下午好", "晚上好", "hello", "hi ", " hey", "在吗")):
        return _chat_plan(_CHAT_INTRO)
    if any(k in q for k in ("谢谢", "感谢", "辛苦了", "thanks", "thank")):
        return _chat_plan("不客气！还需要我帮你分析这个片区的什么指标吗？")
    if any(k in q for k in ("再见", "拜拜", "bye", "下次见")):
        return _chat_plan("再见！随时回来继续分析这个片区。")
    if any(k in q for k in ("你是谁", "你能做什么", "可以做什么", "会做什么", "介绍一下", "有什么功能", "help")):
        return _chat_plan(_CHAT_INTRO)
    return None


def _failed_plan(question: str) -> PlanResult:
    """LLM 不可用或规划失败时的诚实回复：不产出计划、不触发任何分析。

    不再降级到规则引擎——规则版按词表查表产出的计划常常不是用户想要的，
    反而伤害体验；宁可如实告知失败与重试建议。
    """
    task = (question or "").strip().replace("\n", " ")
    if len(task) > 40:
        task = task[:40] + "…"
    return _chat_plan(
        f"抱歉，我这次没能规划「{task}」这个任务（AI 规划调用失败，"
        "可能是服务暂时不可用或未配置）。请稍后重试，或换个更明确的问法。"
    )


def plan_for(
    question: str, history: list[dict] | None = None, conversation_id: str | None = None
) -> PlanResult:
    """优先用 LLM Planner（已配置 key 且调用成功）；失败时不兜底，如实告知。

    history: 多轮对话历史（[{role, content}]），供 LLM 参考上下文。
    conversation_id: 会话 id，透传给 LLM Planner 注入多轮记忆。
    """
    chat = _chat_reply_for(question)
    if chat is not None:
        return chat
    llm_plan = try_plan(question, history, conversation_id)
    if llm_plan is not None:
        return llm_plan
    return _failed_plan(question)
