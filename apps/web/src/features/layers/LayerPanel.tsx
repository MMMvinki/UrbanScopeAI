import { Fragment, useEffect, useRef, useState } from 'react'
import { CheckSquare, ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, Lock, Pencil, Plus, Square, Trash2, Users, X } from 'lucide-react'
import type { DragEvent, ReactNode } from 'react'
import type { LayerAction, LayerGroup, LayerMetadata } from './layerTypes'
import { fetchDataSources, type FolderSource } from '../../lib/api'
import { LayerRow } from './LayerRow'
import { LayerGroupRow } from './LayerGroupRow'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
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

/** 分析生成图层的白话中文展示名（按 id 前缀匹配；基础元素命名，不硬省字数） */
const RESULT_DISPLAY_NAMES: Array<[string, string]> = [
  ['activity_temporal_profile', '昼夜活动强度（100m）'],
  ['commercial_supply', '商业点数量（100m）'],
  ['commercial_vitality', '昼夜×活力匹配（100m）'],
  ['public_service_profile', '设施资源分布（100m）'],
  ['population_workforce_profile', '居住与职住（100m）'],
  ['streetview', '街道绿视率与天空'],
  ['_kde', '核密度分布'],
]

/** 分析图层展示名：命中 id 前缀用白话中文，否则用图层原名 */
function displayNameFor(layer: LayerMetadata): string {
  const hit = RESULT_DISPLAY_NAMES.find(([prefix]) => layer.id.startsWith(prefix))
  return hit ? hit[1] : layer.name
}

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
  const setLayersVisible = useAppStore((s) => s.setLayersVisible)
  const showOnlyLayer = useAppStore((s) => s.showOnlyLayer)
  const removeLayer = useAppStore((s) => s.removeLayer)
  const duplicateLayer = useAppStore((s) => s.duplicateLayer)
  const focusLayer = useAppStore((s) => s.focusLayer)
  const moveLayer = useAppStore((s) => s.moveLayer)
  const moveGroup = useAppStore((s) => s.moveGroup)
  const removeGroup = useAppStore((s) => s.removeGroup)
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
  // 就地重命名：正在编辑的组 id（组头双击改名）
  const [renamingGroupId, setRenamingGroupId] = useState<string | null>(null)
  // 组头拖拽换序：正在拖的组 id（与图层行拖拽互斥）
  const [dragGroupId, setDragGroupId] = useState<string | null>(null)
  // 组展开态：收起的组 id 集合（默认全展开）
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<Set<string>>(new Set())

  // 常显多选：已选图层 id 集合（平台只读图层不可选）；始终可用，不需进入「多选」模式
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // 已选分组 id 集合（组像图层一样可多选；删除操作条同时删被选图层与被选分组）
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<string>>(new Set())
  // shift 范围选择的锚点（最后一次非 shift 点击的行 id；shift+点击 = 选中锚点到该行之间）
  const anchorIdRef = useRef<string | null>(null)
  // 批量分组：正在被分组的图层 id 集合（null = 未打开批量分组）
  const [batchGroupLayers, setBatchGroupLayers] = useState<string[] | null>(null)
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

  /** 清空当前选择 */
  const clearSelection = () => {
    setSelectedIds(new Set())
    setSelectedGroupIds(new Set())
    anchorIdRef.current = null
  }

  /** 用户数据里可选择的图层（非平台只读），按渲染顺序（分组内 + 未分组）排列，供 shift 范围选择 */
  const selectableIds = () => {
    const grouped = new Set(groups.flatMap((group) => group.layerIds))
    const user = layers.filter((layer) => !(layer.source === 'platform' && layer.readonly))
    // 底层模型序是「底→顶」（数组尾=最前层）；面板已翻转为「顶行=最前层」，
    // shift 范围选择 / 全选都按用户看到的可见顺序（前→后）走
    const ordered = [
      ...groups.flatMap((group) => group.layerIds),
      ...user.filter((layer) => !grouped.has(layer.id)).map((layer) => layer.id),
    ]
    return ordered
      .reverse()
      .filter((id) => {
        const layer = layers.find((item) => item.id === id)
        return layer != null && !layer.readonly
      })
  }

  const toggleSelect = (layerId: string, shiftKey = false) => {
    const ids = selectableIds()
    const next = new Set(selectedIds)
    // shift + 点：选中「锚点 → 该行」之间的可选项（锚点=最后一次非 shift 点击）
    if (shiftKey && anchorIdRef.current != null) {
      const a = ids.indexOf(anchorIdRef.current)
      const b = ids.indexOf(layerId)
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a <= b ? [a, b] : [b, a]
        for (let i = lo; i <= hi; i += 1) next.add(ids[i])
        setSelectedIds(next)
        return
      }
    }
    if (next.has(layerId)) next.delete(layerId)
    else next.add(layerId)
    anchorIdRef.current = layerId
    setSelectedIds(next)
  }

  /** 全选 / 取消全选（已全选则清空） */
  const toggleSelectAll = () => {
    const ids = selectableIds()
    const allSelected = selectedIds.size === ids.length && selectedGroupIds.size === groups.length
    if (allSelected) {
      setSelectedIds(new Set())
      setSelectedGroupIds(new Set())
    } else {
      setSelectedIds(new Set(ids))
      setSelectedGroupIds(new Set(groups.map((g) => g.id)))
    }
  }

  /** 点眼睛的成熟交互：有多选且点的是所选行 → 整组一起开关（以该行状态反向翻转）；否则单层开关 */
  const handleRowVisibilityToggle = (layer: LayerMetadata) => {
    if (selectedIds.size > 0 && selectedIds.has(layer.id)) {
      setLayersVisible(Array.from(selectedIds), !layer.visible)
      return
    }
    toggleLayerVisibility(layer.id)
  }

  /** 把所选图层批量移到分组：打开批量分组弹窗 */
  const handleBatchGroup = () => {
    const targets = Array.from(selectedIds)
    if (targets.length === 0) return
    setBatchGroupLayers(targets)
  }

  /** 清空「我的数据」：删除全部非平台只读图层（平台数据保留） */
  const handleClearMyData = async () => {
    const targets = layers.filter((layer) => !(layer.source === 'platform' && layer.readonly))
    if (targets.length === 0) return
    if (!window.confirm(`清空「我的图层」共 ${targets.length} 个？\n将删除这些图层的地图与数据（平台数据不删）。`)) {
      return
    }
    const results = await Promise.allSettled(targets.map((layer) => removeLayer(layer.id)))
    if (results.some((result) => result.status === 'rejected')) window.alert('部分图层删除失败，请重试。')
  }

  /** 批量删除所选图层 + 所选分组：分组删除=移除分组（组内图层移回未分组，不删图层）；图层逐个后端删除 */
  const handleMultiDelete = async () => {
    const targetGroups = groups.filter((g) => selectedGroupIds.has(g.id))
    const targetLayers = layers.filter((layer) => selectedIds.has(layer.id) && !layer.readonly)
    const total = targetGroups.length + targetLayers.length
    if (total === 0) return
    const parts: string[] = []
    if (targetGroups.length) parts.push(`${targetGroups.length} 个分组`)
    if (targetLayers.length) parts.push(`${targetLayers.length} 个图层`)
    if (
      !window.confirm(
        `删除选中的 ${parts.join('、')}？\n分组删除只移除分组（组内图层移回未分组）；图层将彻底移除。`,
      )
    ) {
      return
    }
    // 分组：本地移除（组内图层移回未分组，不删图层本身）
    if (targetGroups.length) targetGroups.forEach((g) => removeGroup(g.id))
    // 图层：逐个后端删除
    const results = await Promise.allSettled(targetLayers.map((layer) => removeLayer(layer.id)))
    const failed = results.filter((result) => result.status === 'rejected')
    if (failed.length > 0) {
      window.alert(`有 ${failed.length} 个图层删除失败，请重试。`)
    }
    clearSelection()
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
        // 单图层分组也走批量分组弹窗（layerIds 长度 1），与多选批量分组共用一套交互
        setBatchGroupLayers([layerId])
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

  // 平台数据（只读底座）固定沉在列表最底独立分组（=地图最底层）；其余（演示/上传/生成 + 用户分组）在上
  const platformLayers = layers.filter((layer) => layer.source === 'platform' && layer.readonly)
  const groupedIds = new Set(groups.flatMap((group) => group.layerIds))
  const ungrouped = layers
    .filter((layer) => !(layer.source === 'platform' && layer.readonly))
    .filter((layer) => !groupedIds.has(layer.id))
  // 可见顺序 = 分组内成员 + 未分组（底层模型序，底→顶，仅拖拽落点换算用；面板渲染已翻转成顶行=最前层）
  const visibleIds = [
    ...groups.flatMap((group) => group.layerIds),
    ...ungrouped.map((layer) => layer.id),
  ]
  // 「我的数据」折叠组计数 = 分组内**真实存在**的成员 + 未分组（剔除已删除图层的幽灵 id）
  const layerIdSet = new Set(layers.map((layer) => layer.id))
  const myDataCount =
    groups.reduce(
      (count, group) => count + group.layerIds.filter((id) => layerIdSet.has(id)).length,
      0,
    ) + ungrouped.length
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
    // 面板已翻转成「顶行=最前层」，底层模型序仍是「底→顶」（数组尾=最前层）。
    // 拖到目标「上方」= 比目标更前 → 模型里插到目标之后；「下方」= 更底 → 插到目标之前。
    // visibleIds 是模型序；fromId 若在目标之前被移除会使目标索引前移 1，先补偿再插。
    const targetIndex = visibleIds.indexOf(target.layerId)
    const fromIndex = visibleIds.indexOf(dragFromId)
    if (targetIndex < 0) return
    const targetAfterRemoval = targetIndex - (fromIndex >= 0 && fromIndex < targetIndex ? 1 : 0)
    const toVisibleIndex = target.position === 'above' ? targetAfterRemoval + 1 : targetAfterRemoval
    moveLayer(dragFromId, toVisibleIndex)
    setDragFromId(null)
    setDropTarget(null)
  }

  const handleDragEnd = () => {
    setDragFromId(null)
    setDragGroupId(null)
    setDropTarget(null)
  }

  // ── 组头交互（对准图层行语义）──────────────────────────────

  /** 组「当前可见」= 组内全部图层可见；组头眼睛据此批量开关整组 */
  const toggleGroupVisibility = (group: LayerGroup) => {
    const allVisible =
      group.layerIds.length > 0 &&
      group.layerIds.every((id) => layers.find((l) => l.id === id)?.visible)
    setLayersVisible(group.layerIds, !allVisible)
  }

  /** 点组名：选中组本身（组像图层一样可多选）；已选则取消。组内成员仍可逐层点选。 */
  const toggleSelectGroup = (group: LayerGroup) => {
    const next = new Set(selectedGroupIds)
    if (next.has(group.id)) next.delete(group.id)
    else next.add(group.id)
    setSelectedGroupIds(next)
  }

  /** 组头拖动把手：记录 dragged group，组头也是 drop 目标（锚点=组内首成员层 id） */
  const handleGroupDragStart = (groupId: string) => (_event: DragEvent<HTMLDivElement>) => {
    setDragGroupId(groupId)
    setDragFromId(null)
    setDropTarget(null)
  }
  const handleGroupDragOver = (groupId: string) => (event: DragEvent<HTMLDivElement>) => {
    if (!dragGroupId && !dragFromId) return
    if (dragGroupId === groupId) return
    if (dragFromId) return // 拖图层时组头不作为额外目标，避免干扰既有的行 drop
    event.preventDefault()
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
    const position = event.clientY < rect.top + rect.height / 2 ? 'above' : 'below'
    setDropTarget((prev) =>
      prev && prev.layerId === groupId && prev.position === position ? prev : { layerId: groupId, position },
    )
  }
  const handleGroupDrop = (groupId: string) => (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (!dragGroupId || dragGroupId === groupId) return
    const target = dropTarget ?? { layerId: groupId, position: 'above' as const }
    setDragGroupId(null)
    setDropTarget(null)
    const group = groups.find((g) => g.id === dragGroupId)
    if (!group || group.layerIds.length === 0) return
    // 组头落点锚点 = 组内首成员，交给 moveGroup 整块重排
    moveGroup(dragGroupId, target.layerId === groupId ? group.layerIds[0] : target.layerId, target.position)
  }
  /** 组头 drop 指示：以某组内部首成员为锚点时显示在组行上 */
  const groupDropIndicator = (group: LayerGroup): 'above' | 'below' | null =>
    dropTarget?.layerId === group.id || dropTarget?.layerId === group.layerIds[0]
      ? dropTarget?.position ?? null
      : null
  const toggleGroupExpand = (groupId: string) => {
    setCollapsedGroupIds((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }

  const renderRow = (layerId: string) => {
    const layer = layers.find((item) => item.id === layerId)
    if (!layer) return null
    return (
      <Fragment key={layer.id}>
        <LayerRow
          layer={layer}
          displayName={displayNameFor(layer)}
          menuOpen={openMenuId === layer.id}
          onToggleMenu={() => setOpenMenuId(openMenuId === layer.id ? null : layer.id)}
          onCloseMenu={() => setOpenMenuId(null)}
          onToggleVisibility={() => handleRowVisibilityToggle(layer)}
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
          selected={selectedIds.has(layer.id)}
          onToggleSelect={(shiftKey) => toggleSelect(layer.id, shiftKey)}
        />
      </Fragment>
    )
  }

  return (
    <div className="panel-content layer-panel">
      <header className="panel-header">
        <div className="panel-header__actions">
          <PanelCollapseButton />
        </div>
      </header>

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
          {/* 「我的数据」折叠组（改动 2）：用户分组 + 未分组图层，折叠态 localStorage 持久化。
              面板翻转为「顶行=地图最前层」，平台只读底座（地图最底层）沉到列表最底。 */}
          {myDataCount > 0 && (
            <div className="layer-group layer-group--user">
              <div className="layer-group__header-row">
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
                  <span className="layer-group__name">我的图层</span>
                  <span className="layer-group__count">{myDataCount}</span>
                </button>
                {/* 图层操作附属到「我的图层」组头：添加图层 / 新建绘制 / 清空图层 */}
                <div className="layer-group__actions">
                  <button
                    type="button"
                    className="button button--ghost button--sm"
                    onClick={() => openUploadModal()}
                    title="添加图层（上传数据 / 链接目录）"
                  >
                    <Plus aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="button button--ghost button--sm"
                    onClick={() => requestDraw()}
                    title="在地图上手绘点/线/面创建图层"
                  >
                    <Pencil aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="button button--ghost button--sm"
                    onClick={() => void handleClearMyData()}
                    title="清空所有我的图层（保留平台数据）"
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </div>
              </div>
              {myDataExpanded && (
                <div
                  className="layer-group__body"
                  onClick={(event) => {
                    // 成熟交互：点「我的图层」非图层的空白处即取消多选（行/按钮/操作条内不取消）
                    const target = event.target as HTMLElement
                    if (target.closest('.layer-row, .layer-select-bar, button, input, label')) return
                    setSelectedIds(new Set())
                    setSelectedGroupIds(new Set())
                  }}
                >
                  {/* 多选操作条：随「我的图层」出现（选中了才显示），单点=选中、shift+点=范围 */}
                  {(selectedIds.size > 0 || selectedGroupIds.size > 0) && (
                    <div className="layer-select-bar">
                      <span className="layer-select-bar__count">已选 {selectedIds.size + selectedGroupIds.size} 个</span>
                      <button
                        type="button"
                        className="button button--ghost button--sm"
                        onClick={toggleSelectAll}
                        title="全选 / 取消全选（平台数据只读不可选）"
                      >
                        {selectedIds.size >= selectableIds().length && selectedGroupIds.size === groups.length ? (
                          <>
                            <Square aria-hidden="true" />
                            取消全选
                          </>
                        ) : (
                          <>
                            <CheckSquare aria-hidden="true" />
                            全选
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        className="button button--secondary button--sm"
                        onClick={handleBatchGroup}
                        title="把所选图层移动到分组 / 新建分组"
                      >
                        <FolderPlus aria-hidden="true" />
                        移到分组
                      </button>
                      <button
                        type="button"
                        className="button button--danger button--sm"
                        onClick={() => void handleMultiDelete()}
                      >
                        <Trash2 aria-hidden="true" />
                        删除
                      </button>
                      <button
                        type="button"
                        className="button button--ghost button--sm"
                        onClick={clearSelection}
                      >
                        <X aria-hidden="true" />
                        取消
                      </button>
                    </div>
                  )}
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
                      {/* 面板翻转为「顶行=最前层」：先未分组（最前）再分组（倒序、组内也倒序） */}
                      {ungrouped.map((layer) => renderRow(layer.id)).reverse()}
                      {[...groups].reverse().map((group) => {
                        const memberIds = [...group.layerIds].filter((id) => layers.some((l) => l.id === id))
                        return (
                          <LayerGroupRow
                            key={group.id}
                            group={group}
                            expanded={!collapsedGroupIds.has(group.id)}
                            onToggleExpand={() => toggleGroupExpand(group.id)}
                            selected={selectedGroupIds.has(group.id)}
                            onToggleSelect={() => toggleSelectGroup(group)}
                            onToggleVisibility={() => toggleGroupVisibility(group)}
                            renaming={renamingGroupId === group.id}
                            onRenameRequest={() => setRenamingGroupId(group.id)}
                            onRenameCommit={() => setRenamingGroupId(null)}
                            onRenameCancel={() => setRenamingGroupId(null)}
                            dragging={dragGroupId === group.id}
                            dropIndicator={groupDropIndicator(group)}
                            onDragStart={handleGroupDragStart(group.id)}
                            onDragOver={handleGroupDragOver(group.id)}
                            onDrop={handleGroupDrop(group.id)}
                            onDragEnd={handleDragEnd}
                          >
                            {memberIds.map((layerId) => renderRow(layerId)).reverse()}
                          </LayerGroupRow>
                        )
                      })}
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
          {/* 平台数据（只读底座）：地图最底层，固定在「我的数据」之下 */}
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
                  {platformLayers.slice().reverse().map((layer) => renderRow(layer.id))}
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
      {batchGroupLayers && (
        <GroupDialog layerIds={batchGroupLayers} onClose={() => setBatchGroupLayers(null)} />
      )}
    </div>
  )
}
