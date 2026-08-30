import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, GripVertical, Lock, MoreHorizontal } from 'lucide-react'
import type { DragEvent, KeyboardEvent, PointerEvent } from 'react'
import type { LayerAction, LayerMetadata } from './layerTypes'
import { LayerMenu } from './LayerMenu'
import { useAppStore } from '../../store/useAppStore'

const sourceLabel: Record<LayerMetadata['source'], string> = {
  platform: '平台数据',
  uploaded: '上传数据',
  generated: '生成数据',
  drawn: '手绘数据',
}

interface LayerRowProps {
  layer: LayerMetadata
  menuOpen: boolean
  onToggleMenu: () => void
  onCloseMenu: () => void
  onToggleVisibility: () => void
  onAction: (action: LayerAction) => void
  /** 就地重命名：true 时名字区域切换为输入框（点击图层名 / 菜单「重命名」进入） */
  renaming: boolean
  onRenameRequest: () => void
  /** 提交/取消都回到 Panel 关闭编辑态（改名字由本组件直接写 store） */
  onRenameCommit: () => void
  onRenameCancel: () => void
  /** 拖拽重排：仅从把手按下可拖；dropIndicator 显示目标缝隙高亮 */
  dragging: boolean
  dropIndicator: 'above' | 'below' | null
  onDragStart: (event: DragEvent<HTMLDivElement>, layerId: string) => void
  onDragOver: (event: DragEvent<HTMLDivElement>, layerId: string) => void
  onDrop: (event: DragEvent<HTMLDivElement>, layerId: string) => void
  onDragEnd: () => void
  /** 多选删除：多选模式下行首显示复选框（平台只读图层禁用）；拖拽把手隐藏 */
  selectMode: boolean
  selected: boolean
  onToggleSelect: () => void
}

export function LayerRow({
  layer,
  menuOpen,
  onToggleMenu,
  onCloseMenu,
  onToggleVisibility,
  onAction,
  renaming,
  onRenameRequest,
  onRenameCommit,
  onRenameCancel,
  dragging,
  dropIndicator,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  selectMode,
  selected,
  onToggleSelect,
}: LayerRowProps) {
  const renameLayer = useAppStore((s) => s.renameLayer)
  // 色块用「生效填充色」：样式面板改色后即时反映；分级时用色带首档色
  const style = useAppStore((s) => s.layerStyles[layer.id])
  const swatchColor =
    style?.styleMode === 'graduated'
      ? (style.visualVariables?.graduated?.palette?.[0] ?? style.fillColor ?? layer.color)
      : (style?.fillColor ?? layer.color)

  // 只有从拖拽把手按下才允许 dragstart（行本身 draggable，但非把手起点一律取消）
  const allowDragRef = useRef(false)
  // 就地重命名：draft + 输入框引用；cancelRef 防止 Escape 后失焦误提交
  const [draft, setDraft] = useState(layer.name)
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef(false)

  // 进入编辑态（点击名字 / 菜单「重命名」）：初始化值并聚焦全选
  useEffect(() => {
    if (renaming) {
      cancelRef.current = false
      setDraft(layer.name)
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [renaming, layer.name])

  const commitRename = () => {
    const name = draft.trim()
    if (name && name !== layer.name) renameLayer(layer.id, name)
    onRenameCommit()
  }

  const cancelRename = () => {
    cancelRef.current = true
    onRenameCancel()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    event.stopPropagation() // 防止 Enter/Escape 冒泡触发面板其他行为
    if (event.key === 'Enter') commitRename()
    else if (event.key === 'Escape') cancelRename()
  }

  const handleBlur = () => {
    if (cancelRef.current) {
      cancelRef.current = false
      return
    }
    commitRename()
  }

  const handleDragStart = (event: DragEvent<HTMLDivElement>) => {
    if (!allowDragRef.current) {
      event.preventDefault()
      return
    }
    allowDragRef.current = false
    onDragStart(event, layer.id)
  }

  // 关键：这里绝不能 preventDefault —— Chrome 对 pointerdown 调 preventDefault 会
  // 认为该指针操作被取消，从而放弃启动原生 HTML5 拖拽（表现为拖不动）。
  // 文本选择用 CSS user-select:none 防，不靠取消事件。
  const handlePointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    allowDragRef.current = true
    if (event.button !== 0) return // 仅左键
    event.stopPropagation()
  }

  const indicatorClass = dropIndicator === 'above' ? 'drop-above' : dropIndicator === 'below' ? 'drop-below' : ''
  const rowClass = [
    'layer-row',
    layer.visible ? 'is-visible' : 'is-hidden',
    dragging ? 'dragging' : '',
    selectMode && selected ? 'is-selected' : '',
    indicatorClass,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={rowClass}
      draggable={!selectMode}
      onDragStart={handleDragStart}
      onDragOver={(event) => onDragOver(event, layer.id)}
      onDrop={(event) => onDrop(event, layer.id)}
      onDragEnd={onDragEnd}
    >
      {/* 多选模式：行首复选框（平台只读禁用）；否则为拖拽把手 */}
      {selectMode ? (
        <label
          className="layer-row__select"
          title={
            layer.readonly
              ? '平台数据只读，不可删除'
              : selected
                ? `取消选择 ${layer.name}`
                : `选择 ${layer.name}`
          }
        >
          <input
            type="checkbox"
            checked={selected}
            disabled={layer.readonly}
            onChange={onToggleSelect}
            aria-label={`选择图层 ${layer.name}`}
          />
        </label>
      ) : (
        <button
          type="button"
          className="layer-row__drag"
          aria-label={`拖动 ${layer.name} 调整顺序`}
          title="按住拖动调整顺序"
          onPointerDown={handlePointerDown}
        >
          <GripVertical aria-hidden="true" />
        </button>
      )}

      <button
        type="button"
        className="layer-row__visibility"
        onClick={onToggleVisibility}
        aria-label={layer.visible ? `隐藏 ${layer.name}` : `显示 ${layer.name}`}
      >
        {layer.visible ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
      </button>

      <span
        className="layer-row__swatch"
        style={{ backgroundColor: swatchColor }}
        aria-hidden="true"
      />

      <div className="layer-row__meta">
        {renaming ? (
          <input
            ref={inputRef}
            className="layer-row__rename-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            aria-label="图层名称"
            spellCheck={false}
          />
        ) : (
          <button type="button" className="layer-row__name" onClick={onRenameRequest} title="单击重命名">
            <span>{layer.name}</span>
            {layer.readonly && (
              <span className="layer-row__lock" title="平台数据只读：数据内容不可改，样式/名字可改">
                <Lock aria-hidden="true" />
              </span>
            )}
            <span className="layer-row__source">
              {layer.kind === 'raster' ? '栅格' : sourceLabel[layer.source]}
            </span>
          </button>
        )}
        <p className="layer-row__sub">
          {layer.geometry} · {layer.featureCount} 个要素 · {layer.crs}
          {layer.lazy && layer.featureCount > 0 && <span className="layer-row__lazy">按需加载</span>}
        </p>
      </div>

      <div className="layer-row__menu-wrap">
        <button
          type="button"
          className="layer-row__more"
          onClick={onToggleMenu}
          aria-label={`${layer.name} 更多操作`}
          aria-expanded={menuOpen}
        >
          <MoreHorizontal aria-hidden="true" />
        </button>
        {menuOpen && (
          <LayerMenu onClose={onCloseMenu} onAction={onAction} readonly={layer.readonly} editable={layer.source === 'drawn' && !layer.readonly} />
        )}
      </div>
    </div>
  )
}
