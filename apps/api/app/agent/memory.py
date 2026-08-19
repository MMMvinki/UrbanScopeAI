"""Agent Memory：记录每次执行生成的图层与结果组，供前端查询。"""

from app.models.result import ResultGroup


class AgentMemory:
    def __init__(self) -> None:
        self.generated_layers: dict[str, object] = {}
        self.result_groups: dict[str, ResultGroup] = {}
        self.evidence: dict[str, list[str]] = {}

    def record(self, intent_id: str, group: ResultGroup, evidence: list[str]) -> None:
        self.result_groups[intent_id] = group
        self.evidence[intent_id] = evidence

    def get_group(self, intent_id: str) -> ResultGroup | None:
        return self.result_groups.get(intent_id)

    def latest_result_group(self) -> ResultGroup | None:
        if not self.result_groups:
            return None
        return list(self.result_groups.values())[-1]


MEMORY = AgentMemory()
