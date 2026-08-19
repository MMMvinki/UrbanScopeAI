import { PanelLeftClose } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'

/** 左栏面板头部右侧的收缩按钮：收起左侧功能面板，地图占满；导航/成果跳转会重新展开 */
export function PanelCollapseButton() {
  const toggleLeftPanel = useAppStore((s) => s.toggleLeftPanel)
  return (
    <button
      type="button"
      className="icon-button icon-button--sm panel-collapse-btn"
      onClick={() => toggleLeftPanel()}
      aria-label="收起左侧面板"
      title="收起面板"
    >
      <PanelLeftClose aria-hidden="true" />
    </button>
  )
}
