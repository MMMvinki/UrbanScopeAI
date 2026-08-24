import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Bot } from 'lucide-react'
import { LeftNav } from './LeftNav'
import { TopBar } from './TopBar'
import { LiquidOrb } from './LiquidOrb'
import { MapWorkspace } from '../features/map/MapWorkspace'
import { MapErrorBoundary } from './MapErrorBoundary'
import { useAppStore } from '../store/useAppStore'
import type { NavKey } from '../store/useAppStore'

interface AppShellProps {
  activeNav: NavKey
  onSelectNav: (nav: NavKey) => void
  /** Agent 浮窗开关（悬浮球点击回调；App 传入 toggleAgent） */
  onToggleAgent: () => void
  /** Agent 浮窗是否打开（浮于地图上方右锚；关闭时右下角显示悬浮球） */
  agentOpen: boolean
  /** 左侧功能面板是否展开（TopBar 开关控制） */
  leftPanelOpen: boolean
  /** Agent 浮窗内容（App 传入 <AgentPanel onClose=…/>） */
  agentPanel: ReactNode
  /** 左侧功能面板内容，由 App 根据当前导航决定 */
  leftPanel: ReactNode
}

/**
 * 整体布局：60px 左侧窄导航 + 360px 左侧功能面板 + 中央地图主工作区。
 * 无右侧常驻成果栏；成果入左栏第五项（成果面板在 360px 左槽位渲染）。
 * Agent 面板为右锚浮窗：absolute 于地图区右上（避让地图工具条），关闭时右下角出现悬浮球。
 */
export function AppShell({
  activeNav,
  onSelectNav,
  onToggleAgent,
  agentOpen,
  leftPanelOpen,
  agentPanel,
  leftPanel,
}: AppShellProps) {
  // Agent 悬浮球：可整体拖动（球 + 上方标签一起移动）。位置持久化 urbanscope.fabPos。
  const fabPos = useAppStore((s) => s.fabPos)
  const setFabPos = useAppStore((s) => s.setFabPos)
  const [dragging, setDragging] = useState(false)
  // 拖拽会话（pointerId/起点/基位）；dragMovedRef 记录是否发生真实移动，供点击吞掉拖完后的 click
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; baseX: number; baseY: number } | null>(null)
  const dragMovedRef = useRef(false)
  // wrap 元素引用：基位/边界都以它为准（button 相对 wrap 恒为 0，不能用 button 的 offsetLeft）
  const fabWrapRef = useRef<HTMLDivElement | null>(null)
  const clampFab = (v: number, lo: number, hi: number) => (hi < lo ? v : Math.max(lo, Math.min(hi, v)))
  // 球上方标签(约33px) + 12px 间隙：拖到顶部时给标签留位，避免标签被 TopBar 遮住
  const LABEL_TOP_SPACE = 48

  // 挂载时校验持久化的拖拽位置：旧版拖拽可能遗留越界/非法坐标，夹回有效区（非法则回退默认右下角）
  useEffect(() => {
    const wrap = fabWrapRef.current
    const parent = wrap?.offsetParent as HTMLElement | null
    if (!fabPos || !wrap || !parent) return
    if (!Number.isFinite(fabPos.x) || !Number.isFinite(fabPos.y)) {
      setFabPos(null)
      return
    }
    const maxX = parent.clientWidth - wrap.offsetWidth - 8
    const maxY = parent.clientHeight - wrap.offsetHeight - 8
    const x = clampFab(fabPos.x, 8, maxX)
    const y = clampFab(fabPos.y, LABEL_TOP_SPACE, maxY)
    if (x !== fabPos.x || y !== fabPos.y) setFabPos({ x, y })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleFabPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    const btn = e.currentTarget
    btn.setPointerCapture(e.pointerId)
    // 基位 = wrap 相对 workspace(offsetParent) 的实际渲染位置。未拖过时 wrap 靠 right/bottom 布局在右下角，
    // offsetLeft/Top 即真实 left/top；拖过后 inline left/top 生效同样一致。保证球始终跟手、不跳位。
    const wrap = btn.parentElement as HTMLElement | null
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      baseX: wrap ? wrap.offsetLeft : 0,
      baseY: wrap ? wrap.offsetTop : 0,
    }
    dragMovedRef.current = false
    setDragging(true)
  }

  const handleFabPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    const dx = e.clientX - drag.startX
    const dy = e.clientY - drag.startY
    // 超过阈值才算拖动（区分点击）
    if (Math.abs(dx) + Math.abs(dy) > 3) dragMovedRef.current = true
    // 移动元素是 wrap（球+标签整组），边界用 workspace(wrap 的 offsetParent) 尺寸，而不是 button 的 offsetParent(=wrap)
    const wrap = e.currentTarget.parentElement as HTMLElement | null
    const parent = (wrap?.offsetParent ?? e.currentTarget.offsetParent) as HTMLElement | null
    const w = wrap?.offsetWidth ?? e.currentTarget.offsetWidth
    const h = wrap?.offsetHeight ?? e.currentTarget.offsetHeight
    const maxX = parent ? parent.clientWidth - w - 8 : 0
    const maxY = parent ? parent.clientHeight - h - 8 : 0
    setFabPos({
      x: clampFab(drag.baseX + dx, 8, maxX),
      y: clampFab(drag.baseY + dy, LABEL_TOP_SPACE, maxY),
    })
  }

  const handleFabPointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== e.pointerId) return
    dragRef.current = null
    setDragging(false)
    // 未移动 = 点击 → 开浮窗；拖过则吞掉随后的 click（在 onClick 里用 dragMovedRef 判断）
    if (!dragMovedRef.current) onToggleAgent()
  }

  return (
    <div className="app-shell">
      <LeftNav active={activeNav} onSelect={onSelectNav} />
      <div className="app-shell__main">
        <TopBar />
        <div className={leftPanelOpen ? 'workspace' : 'workspace is-panel-collapsed'}>
          {leftPanelOpen && (
            <section className="left-panel" aria-label="功能面板">
              {leftPanel}
            </section>
          )}
          <MapErrorBoundary>
            <MapWorkspace />
          </MapErrorBoundary>
          {agentOpen && <div className="agent-float">{agentPanel}</div>}
          {!agentOpen && (
            <div
              ref={fabWrapRef}
              className={dragging ? 'agent-fab-wrap is-dragging' : 'agent-fab-wrap'}
              style={fabPos ? { left: fabPos.x, top: fabPos.y } : undefined}
            >
              <span className="agent-fab__label" aria-hidden="true">
                UrbanScope Agent
              </span>
              <button
                type="button"
                className="agent-fab"
                aria-label="打开 Agent"
                onPointerDown={handleFabPointerDown}
                onPointerMove={handleFabPointerMove}
                onPointerUp={handleFabPointerUp}
                onPointerCancel={handleFabPointerUp}
                onClick={(e) => {
                  // 键盘触发（detail=0）走原生 click 开浮窗；鼠标/触屏点击已在 pointerup 处理，这里吞掉防双击
                  if (e.detail === 0) {
                    onToggleAgent()
                  } else if (dragMovedRef.current) {
                    dragMovedRef.current = false
                  }
                }}
              >
                <LiquidOrb size={84} title="打开 Agent" fallback={<Bot aria-hidden="true" />} />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
