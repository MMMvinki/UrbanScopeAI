import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Eye, EyeOff, Folder, GripVertical } from 'lucide-react'
import type { DragEvent, KeyboardEvent, PointerEvent, ReactNode } from 'react'
import type { LayerGroup } from './layerTypes'
import { useAppStore } from '../../store/useAppStore'

interface LayerGroupRowProps {
  group: LayerGroup
  /** 组体（成员行列表）是否展开 */
  expanded: boolean
  onToggleExpand: () => void
  /** 组被选中（组内全部成员已纳入多选） */
  selected: boolean
  onToggleSelect: () => void
  /** 点眼睛：组内全部图层一起开关 */
  onToggleVisibility: () => void
  /** 双击组名改名 */
  renaming: boolean
  onRenameRequest: () => void
  onRenameCommit: () => void
  onRenameCancel: () => void
  /** 六个点把手拖拽换序（组整块重排） */
  dragging: boolean
  dropIndicator: 'above' | 'below' | null
  onDragStart: (event: DragEvent<HTMLDivElement>) => void
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onDragEnd: () => void
  /** 组体成员（expanded 才渲染） */
  children?: ReactNode
}

/**
 * 图层组头：与图层行同级的交互语义 —— ①眼睛=组内全部开关 ②六个点把手=组整块拖拽换序
 * ③展开箭头=收起/展开组体 ④点组名=选中组（纳入多选）⑤双击组名=改名。
 * 复用 LayerRow 的 `.layer-row__*` 视觉 class，保证与图层行观感一致。
 */
export function LayerGroupRow({
  group,
  expanded,
  onToggleExpand,
  selected,
  onToggleSelect,
  onToggleVisibility,
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
  children,
}: LayerGroupRowProps) {
  const renameGroup = useAppStore((s) => s.renameGroup)
  // 组「当前可见」状态 = 组内全部图层可见；点眼睛据此反向翻转整组。
  // 注意：不要把 filter 放回 selector 里——每次调用都返回新数组引用，会触发
  // useSyncExternalStore 的 getSnapshot 不稳定 → "Maximum update depth exceeded"
  //（run 结束自动建分组后必现）。这里 selector 只取稳定的 layers 引用，用 useMemo 派生。
  const allLayers = useAppStore((s) => s.layers)
  const groupLayers = useMemo(
    () => allLayers.filter((layer) => group.layerIds.includes(layer.id)),
    [allLayers, group.layerIds],
  )
  const allVisible =
    groupLayers.length > 0 && groupLayers.every((layer) => layer.visible)

  // 只有从把手按下才允许 dragstart（同 LayerRow：非把手起点一律取消）
  const allowDragRef = useRef(false)
  // 就地重命名：draft + 输入框引用；cancelRef 防 Escape 后失焦误提交
  const [draft, setDraft] = useState(group.name)
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef(false)

  useEffect(() => {
    if (renaming) {
      cancelRef.current = false
      setDraft(group.name)
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [renaming, group.name])

  const commitRename = () => {
    const name = draft.trim()
    if (name && name !== group.name) renameGroup(group.id, name)
    onRenameCommit()
  }

  const cancelRename = () => {
    cancelRef.current = true
    onRenameCancel()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    event.stopPropagation()
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
    onDragStart(event)
  }

  const handlePointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    allowDragRef.current = true
    if (event.button !== 0) return
    event.stopPropagation()
  }

  const indicatorClass =
    dropIndicator === 'above' ? 'drop-above' : dropIndicator === 'below' ? 'drop-below' : ''
  const rowClass = [
    'layer-row layer-group-row',
    dragging ? 'dragging' : '',
    selected ? 'is-selected' : '',
    indicatorClass,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={rowClass}
      draggable
      onDragStart={handleDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onDragEnd={onDragEnd}
      onClick={(event) => {
        const target = event.target as HTMLElement
        // 点按钮（眼睛/展开/把手/输入框）不触发选组；点组名/空白 = 选中该组
        if (target.closest('button, input, label')) return
        onToggleSelect()
      }}
    >
      <button
        type="button"
        className="layer-row__drag"
        aria-label={`拖动分组 ${group.name} 调整顺序`}
        title="按住拖动调整分组顺序"
        onPointerDown={handlePointerDown}
      >
        <GripVertical aria-hidden="true" />
      </button>

      <button
        type="button"
        className="layer-row__visibility"
        onClick={onToggleVisibility}
        aria-label={allVisible ? `隐藏分组 ${group.name}` : `显示分组 ${group.name}`}
        title={allVisible ? `隐藏分组 ${group.name}` : `显示分组 ${group.name}`}
      >
        {allVisible ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
      </button>

      <div className="layer-row__meta">
        {renaming ? (
          <input
            ref={inputRef}
            className="layer-row__rename-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            aria-label="分组名称"
            spellCheck={false}
          />
        ) : (
          <button
            type="button"
            className="layer-row__name"
            onClick={(event) => {
              event.stopPropagation()
              onToggleSelect()
            }}
            onDoubleClick={(event) => {
              event.stopPropagation()
              onRenameRequest()
            }}
            title="单击选中分组 · 双击重命名"
          >
            <span>{group.name}</span>
            <span className="layer-row__source">
              <Folder aria-hidden="true" /> 分组
            </span>
          </button>
        )}
      </div>

      <button
        type="button"
        className="layer-row__expand"
        onClick={onToggleExpand}
        aria-label={expanded ? `收起分组 ${group.name}` : `展开分组 ${group.name}`}
        aria-expanded={expanded}
        title="展开/收起分组"
      >
        {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
      </button>

      {expanded && <div className="layer-group__body">{children}</div>}
    </div>
  )
}
