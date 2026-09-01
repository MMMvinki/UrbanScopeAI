import { Fragment, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Folder, FolderOpen, ListChecks, Lock, Pencil, Plus, Trash2, Users, X } from 'lucide-react'
import type { DragEvent, ReactNode } from 'react'
import type { LayerAction, LayerMetadata } from './layerTypes'
import { fetchDataSources, type FolderSource } from '../../lib/api'
import { LayerRow } from './LayerRow'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import { LayerLegend } from './LayerLegend'
import { useAppStore } from '../../store/useAppStore'
import { StylePanel } from './StylePanel'
import { PreviewModal } from './PreviewModal'
import { GroupDialog } from './GroupDialog'
import { FolderSourcesSection } from './FolderSourcesSection'

type ModalKind = 'style' | 'preview' | 'group'

/** 拖拽重排的落点状态：目标行 + 插入到其上方/下方（gap 高亮） */
interface DropTarget {
  layerId: string
  position: 'above' | 'below'
}

/* ── 「我的数据」文件路径视图（第 5 项）：已启用图层按来源文件夹归类 ────── */

interface PathDir {
  kind: 'dir'
  name: string
  children: PathNode[]
}
interface PathLeaf {
  kind: 'layer'
  layerId: string
}
type PathNode = PathDir | PathLeaf

function isPathDir(n: PathNode): n is PathDir {
  return n.kind === 'dir'
}

function countPathLeaves(nodes: PathNode[]): number {
  return nodes.reduce((sum, n) => sum + (isPathDir(n) ? countPathLeaves(n.children) : 1), 0)
}

/** 已启用图层按路径建树：folder 用 rel_path 细分子文件夹，其余回退 data_path；无任何路径归「未关联文件夹」。
 *  只基于已在「我的数据」的图层（=已启用数据），不会再混入未启用文件。
 *  noPathDirName 传 null 时，无路径图层直接平铺为根级叶子（用于「未关联文件夹」组，
 *  避免组 header 与内层目录重复一层同名目录）。 */
function buildLayerPathTree(
  layers: LayerMetadata[],
  resolvePath?: (l: LayerMetadata) => string | null,
  noPathDirName: string | null = '未关联文件夹',
): PathDir {
  const root: PathDir = { kind: 'dir', name: '', children: [] }
  const noPath: LayerMetadata[] = []
  for (const l of layers) {
    const raw = resolvePath ? resolvePath(l) : (l.relPath ?? l.dataPath)
    if (!raw) {
      noPath.push(l)
      continue
    }
    const parts = raw.replace(/\\/g, '/').split('/').filter(Boolean)
    let node = root
    for (let i = 0; i < parts.length - 1; i++) {
      let child = node.children.find((c) => isPathDir(c) && c.name === parts[i]) as PathDir | undefined
      if (!child) {
        child = { kind: 'dir', name: parts[i], children: [] }
        node.children.push(child)
      }
      node = child
    }
    node.children.push({ kind: 'layer', layerId: l.id })
  }
  if (noPath.length > 0) {
    if (noPathDirName) {
      root.children.push({
        kind: 'dir',
        name: noPathDirName,
        children: noPath.map((l) => ({ kind: 'layer', layerId: l.id })),
      })
    } else {
      const leaves: PathLeaf[] = noPath.map((l) => ({ kind: 'layer', layerId: l.id }))
      root.children.push(...leaves)
    }
  }
  const sortNodes = (nodes: PathNode[]): PathNode[] => {
    nodes.sort((a, b) => {
      const aDir = isPathDir(a) ? 0 : 1
      const bDir = isPathDir(b) ? 0 : 1
      if (aDir !== bDir) return aDir - bDir
      const an = isPathDir(a) ? a.name : a.layerId
      const bn = isPathDir(b) ? b.name : b.layerId
      return an.localeCompare(bn, 'zh')
    })
    nodes.forEach((n) => {
      if (isPathDir(n)) sortNodes(n.children)
    })
    return nodes
  }
  sortNodes(root.children)
  return root
}

/** 规范化路径：统一正斜杠、去重复斜杠和 `.` 段、去末尾斜杠；Windows 盘符小写化（用于不区分大小写比较）。 */
function normalizePath(p: string): string {
  let s = (p ?? '').replace(/\\/g, '/')
  const isUnc = s.startsWith('//')
  s = s.replace(/\/{2,}/g, '/')
  if (isUnc) s = `/${s}`
  const stack: string[] = []
  for (const part of s.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      stack.pop()
      continue
    }
    stack.push(part)
  }
  let out = stack.join('/').replace(/\/+$/, '')
  out = out.replace(/^([a-zA-Z]):/, (_m, drive: string) => drive.toLowerCase() + ':')
  if (isUnc) out = `/${out}`
  return out
}

/** Windows 路径按不区分大小写比较（驱动器号 + 目录大小写差异都兼容） */
function pathKey(p: string): string {
  return normalizePath(p).toLowerCase()
}

/** 把图层的 data_path 匹配到某个文件夹数据源根目录（链接目录/下载目录均为 folder 源）。
 *  多个根目录都命中时选最长根目录（嵌套来源不归到外层）。
 *  命中返回 [source_id, 相对该源根的 rel_path]，否则返回 null。 */
function matchLayerSource(l: LayerMetadata, sources: FolderSource[]): [string, string] | null {
  if (!l.dataPath) return null
  const dp = normalizePath(l.dataPath)
  const dpKey = pathKey(l.dataPath)
  let best: { sourceId: string; rel: string; rootLen: number } | null = null
  for (const s of sources) {
    const root = normalizePath(s.root_path)
    const rootKey = pathKey(s.root_path)
    if (dpKey === rootKey || dpKey.startsWith(rootKey + '/')) {
      const rel = dpKey === rootKey ? '' : dp.slice(root.length + 1)
      if (!best || root.length > best.rootLen) {
        best = { sourceId: s.source_id, rel, rootLen: root.length }
      }
    }
  }
  if (!best) return null
  // rel_path（folder 源图层登记的相对路径）优先，比从 data_path 反推更稳定
  return [best.sourceId, l.relPath ?? best.rel]
}

/** 递归渲染文件路径目录节点（复用 folder-tree-dir 外观；叶为图层行 renderLeaf）。 */
function PathDirNode({
  node,
  depth,
  renderLeaf,
}: {
  node: PathDir
  depth: number
  renderLeaf: (layerId: string) => ReactNode
}) {
  const [expanded, setExpanded] = useState(depth < 1)
  return (
    <div className="folder-tree-dir">
      <div className="folder-tree-dir__row">
        <button
          type="button"
          className="folder-tree-dir__header"
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          <Folder aria-hidden="true" />
          <span className="folder-tree-dir__name">{node.name}</span>
          <span className="folder-tree-dir__count">{countPathLeaves(node.children)} 图层</span>
        </button>
      </div>
      {expanded &&
        node.children.map((child) =>
          isPathDir(child) ? (
            <PathDirNode key={child.name} node={child} depth={depth + 1} renderLeaf={renderLeaf} />
          ) : (
            <Fragment key={child.layerId}>{renderLeaf(child.layerId)}</Fragment>
          ),
        )}
    </div>
  )
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
  const requestDraw = useAppStore((s) => s.requestDraw)
  const requestEditDrawnLayer = useAppStore((s) => s.requestEditDrawnLayer)

  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [modal, setModal] = useState<{ layerId: string; kind: ModalKind } | null>(null)
  const [dragFromId, setDragFromId] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null)
  // 就地重命名：正在编辑的图层 id
  const [renamingId, setRenamingId] = useState<string | null>(null)

  // 多选删除：多选模式 + 已选图层 id 集合（平台只读图层不可选）
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // 「我的数据」视图切换（第 5 项）：图层视图（分组平铺） / 文件路径视图（已启用图层按来源文件夹归类）
  const [myDataView, setMyDataView] = useState<'layer' | 'path'>('layer')
  // 文件路径视图用：文件夹数据源根目录（含下载目录），把图层 data_path 归类到对应来源
  // 数据源版本号变化（新增/取消链接、下载目录保存、重扫）时重新拉取，归组保持最新
  const folderSourcesTick = useAppStore((s) => s.folderSourcesTick)
  const [folderSources, setFolderSources] = useState<FolderSource[]>([])
  useEffect(() => {
    fetchDataSources()
      .then((r) => setFolderSources(r.sources))
      .catch(() => {})
  }, [folderSourcesTick])

  const enterSelectMode = () => {
    setSelectMode(true)
    setSelectedIds(new Set())
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const toggleSelect = (layerId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(layerId)) next.delete(layerId)
      else next.add(layerId)
      return next
    })
  }

  /** 批量删除所选图层：只删非只读图层；逐个走后端彻底删除，部分失败时提示 */
  const handleMultiDelete = async () => {
    const targets = layers.filter((layer) => selectedIds.has(layer.id) && !layer.readonly)
    if (targets.length === 0) return
    const names = targets.map((layer) => `「${layer.name}」`).join('、')
    if (
      !window.confirm(
        `删除选中的 ${targets.length} 个图层？\n${names}\n这些图层将从地图与数据中移除。`,
      )
    ) {
      return
    }
    const results = await Promise.allSettled(targets.map((layer) => removeLayer(layer.id)))
    const failed = results.filter((result) => result.status === 'rejected')
    if (failed.length > 0) {
      window.alert(`有 ${failed.length} 个图层删除失败，请重试。`)
    }
    exitSelectMode()
  }

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
      case 'data-path':
        if (layer?.dataPath) {
          window.alert(`该图层源数据路径：\n${layer.dataPath}`)
        }
        break
      case 'edit':
        // 手绘图层：进入地图编辑会话（载入几何 → select 拖顶点 → 完成时 PUT 保存）
        if (layer?.source === 'drawn' && !layer.readonly) requestEditDrawnLayer(layerId)
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
  // 「我的数据」已启用图层全集（平台只读底座之外），文件路径视图按来源文件夹归类
  const myDataLayers = layers.filter((l) => !(l.source === 'platform' && l.readonly))
  // 文件路径视图：把已启用图层按「匹配到的文件夹数据源」分组，组内再按 rel_path 建树；无来源归「未关联文件夹」
  const srcById = new Map(folderSources.map((s) => [s.source_id, s]))
  // pathGroups 以 source_id 为 key（alias 只做展示）：同名来源不合并，React key 不冲突
  const pathGroups: Array<{ sourceId: string; alias: string; layers: LayerMetadata[] }> = []
  const unmatchedLayers: LayerMetadata[] = []
  for (const l of myDataLayers) {
    const m = matchLayerSource(l, folderSources)
    if (m) {
      const sourceId = m[0]
      const alias = srcById.get(sourceId)?.alias ?? sourceId
      const g = pathGroups.find((group) => group.sourceId === sourceId)
      if (g) g.layers.push(l)
      else pathGroups.push({ sourceId, alias, layers: [l] })
    } else {
      unmatchedLayers.push(l)
    }
  }
  // 「未关联文件夹」固定最后（排在所有已匹配来源之后），避免与外层/内层同名目录混淆
  const layerPathGroups = myDataView === 'path'
    ? pathGroups.map((g) => ({
        sourceId: g.sourceId,
        alias: g.alias,
        layers: g.layers,
        tree: buildLayerPathTree(g.layers, (l) => matchLayerSource(l, folderSources)?.[1] ?? null),
      })).concat(
        unmatchedLayers.length
          ? [{ sourceId: '__unmatched__', alias: '未关联文件夹', layers: unmatchedLayers, tree: buildLayerPathTree(unmatchedLayers, undefined, null) }]
          : [],
      )
    : null

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
          selectMode={selectMode}
          selected={selectedIds.has(layer.id)}
          onToggleSelect={() => toggleSelect(layer.id)}
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
          <button
            type="button"
            className="button button--secondary button--sm"
            onClick={() => requestDraw()}
            title="在地图上手绘点/线/面创建图层"
          >
            <Pencil aria-hidden="true" />
            新建绘制
          </button>
          <button
            type="button"
            className={
              selectMode ? 'button button--secondary button--sm is-active' : 'button button--secondary button--sm'
            }
            onClick={() => (selectMode ? exitSelectMode() : enterSelectMode())}
            title="勾选多个图层后批量删除"
          >
            <ListChecks aria-hidden="true" />
            {selectMode ? '退出多选' : '多选'}
          </button>
          <PanelCollapseButton />
        </div>
      </header>

      {/* 多选删除操作条：显示已选数量 + 批量删除 + 完成 */}
      {selectMode && (
        <div className="layer-select-bar">
          <span className="layer-select-bar__count">
            已选 {selectedIds.size} 个图层{selectedIds.size > 0 ? '（平台数据只读不可选）' : ''}
          </span>
          <button
            type="button"
            className="button button--danger button--sm"
            disabled={selectedIds.size === 0}
            onClick={() => void handleMultiDelete()}
          >
            <Trash2 aria-hidden="true" />
            删除所选
          </button>
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={exitSelectMode}
          >
            <X aria-hidden="true" />
            完成
          </button>
        </div>
      )}

      {/* 数据目录 + 图层列表统一滚动，避免数据多时被裁掉、看不到滚动条 */}
      <div className="layer-panel__scroll scroll-area">
        <FolderSourcesSection />

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
                  <div className="layer-view-toggle" role="tablist" aria-label="我的数据视图">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={myDataView === 'layer'}
                      className={myDataView === 'layer' ? 'is-active' : ''}
                      onClick={() => setMyDataView('layer')}
                      title="按图层分组平铺查看"
                    >
                      图层
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={myDataView === 'path'}
                      className={myDataView === 'path' ? 'is-active' : ''}
                      onClick={() => setMyDataView('path')}
                      title="按源数据文件路径组织查看"
                    >
                      文件路径
                    </button>
                  </div>
                  {myDataView === 'layer' ? (
                    <>
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
                    </>
                  ) : (layerPathGroups ?? []).map((g) => (
                    <div className="layer-group" key={g.sourceId}>
                      <div className="layer-group__header">
                        {g.alias === '未关联文件夹' ? (
                          <FolderOpen aria-hidden="true" />
                        ) : (
                          <Folder aria-hidden="true" />
                        )}
                        <span className="layer-group__name">{g.alias}</span>
                        <span className="layer-group__count">{g.layers.length} 图层</span>
                      </div>
                      {g.tree.children.map((child) =>
                        isPathDir(child) ? (
                          <PathDirNode key={child.name} node={child} depth={0} renderLeaf={renderRow} />
                        ) : (
                          <Fragment key={child.layerId}>{renderRow(child.layerId)}</Fragment>
                        ),
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      </div>

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
