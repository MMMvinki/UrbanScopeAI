import { useEffect } from 'react'
import { AppShell } from './components/AppShell'
import { EvalAdminPage } from './features/eval/EvalAdminPage'
import { AgentPanel } from './features/agent/AgentPanel'
import { LayerPanel } from './features/layers/LayerPanel'
import { ToolsPanel } from './features/tools/ToolsPanel'
import { SkillPanel } from './features/skills/SkillPanel'
import { ProjectPanel } from './features/project/ProjectPanel'
import { ResultsPanel } from './features/results/ResultsPanel'
import { ApprovalModal } from './features/agent/ApprovalModal'
import { UploadModal } from './features/layers/UploadModal'
import { useAppStore, type NavKey } from './store/useAppStore'
import './App.css'

export default function App() {
  const activeNav = useAppStore((s) => s.activeNav)
  const setActiveNav = useAppStore((s) => s.setActiveNav)
  const agentOpen = useAppStore((s) => s.agentOpen)
  const toggleAgent = useAppStore((s) => s.toggleAgent)
  const leftPanelOpen = useAppStore((s) => s.leftPanelOpen)
  const toggleLeftPanel = useAppStore((s) => s.toggleLeftPanel)
  const hydrateData = useAppStore((s) => s.hydrateData)
  const refreshProjects = useAppStore((s) => s.refreshProjects)

  // 挂载时拉取后端图层 / Skill / 项目（后端未启动则保留本地演示数据）
  useEffect(() => {
    void hydrateData()
    void refreshProjects()
  }, [hydrateData, refreshProjects])

  // 导航切换：Agent 已移出左栏成右锚浮窗 → 悬浮球/浮窗 X 触发弹/收，其余才切面板
  // 点当前已激活图标 = toggle 左栏（展开↔收缩）；点其他图标 = 切面板并展开
  const handleNav = (key: NavKey) => {
    if (key === 'agent') toggleAgent()
    else if (key === 'eval') setActiveNav('eval')
    else if (key === activeNav) toggleLeftPanel()
    else setActiveNav(key)
  }

  const leftPanel = {
    project: <ProjectPanel />,
    data: <LayerPanel />,
    tools: <ToolsPanel />,
    skills: <SkillPanel />,
    results: <ResultsPanel />,
    // activeNav 的 'agent' 已被 handleNav 路由走（不进入 setActiveNav），此处排除
  }[activeNav as Exclude<NavKey, 'agent'>]

  if (activeNav === 'eval') {
    // 独立评测管理后台：整页替换 AppShell（与地图工作区分离），自带返回入口
    return (
      <>
        <EvalAdminPage onExit={() => setActiveNav('data')} />
        <ApprovalModal />
        <UploadModal />
      </>
    )
  }

  return (
    <>
      <AppShell
        activeNav={activeNav}
        onSelectNav={handleNav}
        onToggleAgent={toggleAgent}
        agentOpen={agentOpen}
        leftPanelOpen={leftPanelOpen}
        agentPanel={<AgentPanel onClose={toggleAgent} />}
        leftPanel={leftPanel}
      />
      <ApprovalModal />
      <UploadModal />
    </>
  )
}
