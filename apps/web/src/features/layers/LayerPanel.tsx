import { Fragment, useState } from 'react'
import { ChevronDown, ChevronRight, Folder, Lock, Plus, Users } from 'lucide-react'
import type { DragEvent } from 'react'
import type { LayerAction } from './layerTypes'
import { LayerRow } from './LayerRow'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import { LayerLegend } from './LayerLegend'
import { useAppStore } from '../../store/useAppStore'
import { StylePanel } from './StylePanel'
import { PreviewModal } from './PreviewModal'
import { GroupDialog } from './GroupDialog'

type ModalKind = 'style' | 'preview' | 'group'

/** 拖拽重排的落点状态：目标行 + 插入到其上方/下方（gap 高亮） */
interface DropTarget {
  layerId: string
  position: 'above' | 'below'
}

export function LayerPanel() {
  const layers = useAppStore((s) => s.layers)
  const groups = useAppStore((s) => s.groups)
  const toggleLayerVisibility = useAppStore((s) => s.toggleLayerVisibility)
  const showOnlyLayer = useAppStore((s) => s.showOnlyLayer)
  const removeLayer = useAppStore((s) => s.removeLayer)
  const duplicateLayer = useAppStore((s) => s.duplicateLayer)
  const focusLayer = useAppStore((s) => s.focusLayer)
  const moveLayer = useAppStore((s) => s.moveLayer)
  const openUploadModal = useAppStore((s) => s.openUploadModal)
  const hydrateData = useAppStore((s) => s.hydrateData)

  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [modal, setModal] = useState<{ layerId: string; kind: ModalKind } | null>(null)
  const [dragFromId, setDragFromId] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null)
  // 就地重命名：正在编辑的图层 id
  const [renamingId, setRenamingId] = useState<string | null>(null)

  // 平台数据只读图层：折叠展开状态（默认展开）
  const [platformExpanded, setPlatformExpanded] = useState(true)

  // 「我的数据」折叠组（改动 2）：平台数据之外全部（用户分组 + 未分组图层），折叠态持久化
  const [myDataExpanded, setMyDataExpanded] = useState(() => {
    try {
      return localStorage.getItem('urbanscope.myDataCollapsed') !== '1'
    } catch {
      return true
    }
  })

  const toggleMyData = () => {
    const next = !myDataExpanded
    setMyDataExpanded(next)
    try {
      localStorage.setItem('urbanscope.myDataCollapsed', next ? '0' : '1')
    } catch {
      // localStorage 不可用时忽略（仅不持久化，不影响交互）
    }
  }

  const handleAction = (layerId: string, action: LayerAction) => {
    const layer = layers.find((item) => item.id === layerId)
    // 防御：只读平台图层不可删除（菜单已隐藏，键盘/快捷键等直达路径兜底）
    if (action === 'delete' && layer?.readonly) {
      window.alert('平台数据只读，不可删除。其颜色、名称等样式仍可修改。')
      setOpenMenuId(null)
      return
    }
    switch (action) {
      case 'zoom':
        focusLayer(layerId)
        break
      case 'show-only':
        showOnlyLayer(layerId)
        break
      case 'style':
        setModal({ layerId, kind: 'style' })
        break
      case 'duplicate':
        duplicateLayer(layerId)
        break
      case 'rename':
        setRenamingId(layerId)
        break
      case 'group':
        setModal({ layerId, kind: 'group' })
        break
      case 'preview':
        setModal({ layerId, kind: 'preview' })
        break
      case 'delete':
        if (
          window.confirm(
            `删除图层「${layer?.name ?? layerId}」？\n该图层将从地图与数据中移除。`,
          )
        ) {
          // 改动 6：先删后端磁盘文件+注册表，成功才移出前端 store（否则刷新复活）
          void removeLayer(layerId).catch((error: unknown) => {
            window.alert(`删除失败：${error instanceof Error ? error.message : String(error)}`)
          })
        }
        break
    }
    setOpenMenuId(null)
  }

  // 平台数据（只读底座）固定在顶部独立分组；其余（演示/上传/生成 + 用户分组）保持原有渲染
  const platformLayers = layers.filter((layer) => layer.source === 'platform' && layer.readonly)
  const groupedIds = new Set(groups.flatMap((group) => group.layerIds))
  const ungrouped = layers
    .filter((layer) => !(layer.source === 'platform' && layer.readonly))
    .filter((layer) => !groupedIds.has(layer.id))
  // 可见顺序 = 分组内成员 + 未分组（与渲染顺序一致，拖拽落点换算索引用）
  const visibleIds = [
    ...groups.flatMap((group) => group.layerIds),
    ...ungrouped.map((layer) => layer.id),
  ]
  // 「我的数据」折叠组计数 = 分组内成员 + 未分组
  const myDataCount =
    groups.reduce((count, group) => count + group.layerIds.length, 0) + ungrouped.length

  const handleDragStart = (_event: DragEvent<HTMLDivElement>, layerId: string) => {
    setDragFromId(layerId)
    setDropTarget(null)
  }

  const handleDragOver = (event: DragEvent<HTMLDivElement>, layerId: string) => {
    if (!dragFromId || layerId === dragFromId) return
    event.preventDefault() // 允许 drop
    // 指针落在行上半 → 插到该行上方；下半 → 插到该行下方
    const rect = event.currentTarget.getBoundingClientRect()
    const position = event.clientY < rect.top + rect.height / 2 ? 'above' : 'below'
    setDropTarget((prev) =>
      prev && prev.layerId === layerId && prev.position === position ? prev : { layerId, position },
    )
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>, layerId: string) => {
    event.preventDefault()
    if (!dragFromId || layerId === dragFromId) return
    const target = dropTarget ?? { layerId, position: 'above' as const }
    const targetIndex = visibleIds.indexOf(target.layerId)
    if (targetIndex < 0) return
    // 换算到可见序列的插入索引（下方 = 目标行之后）
    const toVisibleIndex = target.position === 'below' ? targetIndex + 1 : targetIndex
    moveLayer(dragFromId, toVisibleIndex)
    setDragFromId(null)
    setDropTarget(null)
  }

  const handleDragEnd = () => {
    setDragFromId(null)
    setDropTarget(null)
  }

  const renderRow = (layerId: string) => {
    const layer = layers.find((item) => item.id === layerId)
    if (!layer) return null
    return (
      <Fragment key={layer.id}>
        <LayerRow
          layer={layer}
          menuOpen={openMenuId === layer.id}
          onToggleMenu={() => setOpenMenuId(openMenuId === layer.id ? null : layer.id)}
          onCloseMenu={() => setOpenMenuId(null)}
          onToggleVisibility={() => toggleLayerVisibility(layer.id)}
          onAction={(action) => handleAction(layer.id, action)}
          renaming={renamingId === layer.id}
          onRenameRequest={() => setRenamingId(layer.id)}
          onRenameCommit={() => setRenamingId(null)}
          onRenameCancel={() => setRenamingId(null)}
          dragging={dragFromId === layer.id}
          dropIndicator={dropTarget?.layerId === layer.id ? dropTarget.position : null}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          onDragEnd={handleDragEnd}
        />
        {/* 分级设色图例：ArcGIS 式色块 + 数值区间，紧跟图层行下方，可下拉收起 */}
        <LayerLegend layerId={layer.id} />
      </Fragment>
    )
  }

  return (
    <div className="panel-content layer-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">数据图层</h2>
        <div className="panel-header__actions">
          <button
            type="button"
            className="button button--secondary button--sm"
            onClick={() => openUploadModal()}
          >
            <Plus aria-hidden="true" />
            添加数据
          </button>
          <PanelCollapseButton />
        </div>
      </header>

      {layers.length === 0 ? (
        <div className="empty-state">
          <p className="empty-state__title">暂无图层</p>
          <p className="empty-state__hint">上传数据文件，或启用平台数据。</p>
          <div className="empty-state__actions">
            <button
              type="button"
              className="button button--secondary button--sm"
              onClick={() => openUploadModal()}
            >
              上传数据
            </button>
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={() => void hydrateData()}
            >
              平台数据
            </button>
          </div>
        </div>
      ) : (
        <div className="layer-list scroll-area">
          {/* 平台数据（只读底座）：真实深圳数据，折叠可收起 */}
          {platformLayers.length > 0 && (
            <div className="layer-group layer-group--platform">
              <button
                type="button"
                className="layer-group__header layer-group__header--button"
                onClick={() => setPlatformExpanded((expanded) => !expanded)}
                aria-expanded={platformExpanded}
              >
                {platformExpanded ? (
                  <ChevronDown aria-hidden="true" />
                ) : (
                  <ChevronRight aria-hidden="true" />
                )}
                <Lock aria-hidden="true" />
                <span className="layer-group__name">平台数据</span>
                <span className="layer-group__count">{platformLayers.length}</span>
              </button>
              {platformExpanded && (
                <div className="layer-group__body">
                  {platformLayers.map((layer) => renderRow(layer.id))}
                </div>
              )}
            </div>
          )}
          {/* 「我的数据」折叠组（改动 2）：用户分组 + 未分组图层，折叠态 localStorage 持久化 */}
          {myDataCount > 0 && (
            <div className="layer-group layer-group--user">
              <button
                type="button"
                className="layer-group__header layer-group__header--button"
                onClick={toggleMyData}
                aria-expanded={myDataExpanded}
              >
                {myDataExpanded ? (
                  <ChevronDown aria-hidden="true" />
                ) : (
                  <ChevronRight aria-hidden="true" />
                )}
                <Users aria-hidden="true" />
                <span className="layer-group__name">我的数据</span>
                <span className="layer-group__count">{myDataCount}</span>
              </button>
              {myDataExpanded && (
                <div className="layer-group__body">
                  {groups.map((group) => (
                    <div className="layer-group" key={group.id}>
                      <div className="layer-group__header">
                        <Folder aria-hidden="true" />
                        <span className="layer-group__name">{group.name}</span>
                        <span className="layer-group__count">{group.layerIds.length}</span>
                      </div>
                      {group.layerIds.map((layerId) => renderRow(layerId))}
                    </div>
                  ))}
                  {ungrouped.map((layer) => renderRow(layer.id))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {modal?.kind === 'style' && (
        <StylePanel layerId={modal.layerId} onClose={() => setModal(null)} />
      )}
      {modal?.kind === 'preview' && (
        <PreviewModal layerId={modal.layerId} onClose={() => setModal(null)} />
      )}
      {modal?.kind === 'group' && (
        <GroupDialog layerId={modal.layerId} onClose={() => setModal(null)} />
      )}
    </div>
  )
}
