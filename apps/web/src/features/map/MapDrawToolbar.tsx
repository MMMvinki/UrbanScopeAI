import { MousePointer2, Pencil, Redo2, Square, Undo2, X } from 'lucide-react'
import type { DrawMode } from './DrawController'

interface MapDrawToolbarProps {
  /** 是否处于绘制会话（未开始=显示「绘制」按钮；已开始=显示模式 + 完成/取消） */
  active: boolean
  /** 当前绘制模式 */
  mode: DrawMode
  /** 是否已有已绘要素（完成按钮的可用性） */
  hasFeatures: boolean
  /** 撤销/重做可用性（仅绘制会话内 true） */
  canUndo: boolean
  canRedo: boolean
  onStart: () => void
  onModeChange: (mode: DrawMode) => void
  onFinish: () => void
  onCancel: () => void
  onUndo: () => void
  onRedo: () => void
}

/** 地图绘制工具栏：入口「绘制」→ 展开点/线/面三档 + 编辑(select) + 撤销/重做 + 完成/取消。 */
export function MapDrawToolbar({
  active,
  mode,
  hasFeatures,
  canUndo,
  canRedo,
  onStart,
  onModeChange,
  onFinish,
  onCancel,
  onUndo,
  onRedo,
}: MapDrawToolbarProps) {
  if (!active) {
    return (
      <button type="button" className="map-draw__start" onClick={onStart} title="在地图上手绘点/线/面创建图层">
        <Pencil aria-hidden="true" />
        绘制
      </button>
    )
  }

  const modes: Array<{ key: DrawMode; label: string; title: string; icon: typeof MousePointer2 }> = [
    { key: 'point', label: '点', title: '绘制点要素（点击落点）', icon: MousePointer2 },
    { key: 'linestring', label: '线', title: '绘制线要素（点击连点，双击结束）', icon: MousePointer2 },
    { key: 'polygon', label: '面', title: '绘制面要素（点击连点，双击闭合）', icon: MousePointer2 },
  ]

  return (
    <div className="map-draw__bar">
      <div className="map-draw__modes">
        {modes.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.key}
              type="button"
              className={`map-draw__mode${mode === item.key ? ' is-active' : ''}`}
              onClick={() => onModeChange(item.key)}
              title={item.title}
            >
              <Icon aria-hidden="true" />
              {item.label}
            </button>
          )
        })}
        <button
          type="button"
          className={`map-draw__mode${mode === 'select' ? ' is-active' : ''}`}
          onClick={() => onModeChange('select')}
          title="编辑：选中要素并拖拽顶点调整"
        >
          <MousePointer2 aria-hidden="true" />
          编辑
        </button>
      </div>
      <div className="map-draw__history">
        <button
          type="button"
          className="map-draw__history-btn"
          onClick={onUndo}
          disabled={!canUndo}
          title="撤销（Ctrl+Z）"
        >
          <Undo2 aria-hidden="true" />
        </button>
        <button
          type="button"
          className="map-draw__history-btn"
          onClick={onRedo}
          disabled={!canRedo}
          title="重做（Ctrl+Y / Ctrl+Shift+Z）"
        >
          <Redo2 aria-hidden="true" />
        </button>
      </div>
      <button
        type="button"
        className="map-draw__finish"
        onClick={onFinish}
        disabled={!hasFeatures}
        title={hasFeatures ? '保存已绘制的要素' : '先在地图上绘制要素'}
      >
        <Square aria-hidden="true" />
        完成
      </button>
      <button type="button" className="map-draw__cancel" onClick={onCancel} title="取消绘制并清空">
        <X aria-hidden="true" />
        取消
      </button>
    </div>
  )
}
