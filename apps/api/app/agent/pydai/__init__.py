"""PydanticAI 迁移模块：用 PydanticAI 原生 loop 重写 planner/executor。

产品层全部保留（三道门 / 样式短路 / execute_code 子进程 / widget 组装 / 记忆 /
RunArchive / planner 领域规整），只是把「手写工具循环」换成框架的 Agent loop。
无轮次上限：模型返回无工具调用的消息即终结。

入口：
- pa_try_plan(question, history, conversation_id) -> PlanResult | None
- pa_try_execute(request, ctx, emit) -> ExecutionResult | None

由 llm_planner.try_plan / llm_executor.try_execute 在 PA_AGENT=1（默认）时调用；
PA_AGENT=0 走旧 loop。见各自模块 docstring。
"""
