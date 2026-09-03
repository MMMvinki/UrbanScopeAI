import { useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  CircleOff,
  Download,
  Folder,
  FolderOpen,
  Link2,
  PlayCircle,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import {
  disableAllDatasets,
  disableDataset,
  enableAllDatasets,
  enableDataset,
  fetchDataSources,
  fetchDownloadFolder,
  rescanDataFolder,
  unlinkDataFolder,
  type FolderDataset,
  type FolderSource,
} from '../../lib/api'
import { refreshLayersIncremental, useAppStore } from '../../store/useAppStore'
import { DownloadFolderModal } from './DownloadFolderModal'

const STATUS_LABEL: Record<string, string> = {
  enableable: '可直接启用',
  query_only: '仅可查询',
  unavailable: '暂不可用',
  discovered: '已发现',
}
const STATUS_BADGE: Record<string, string> = {
  enableable: 'badge badge--success',
  query_only: 'badge badge--info',
  unavailable: 'badge badge--neutral',
  discovered: 'badge badge--warning',
}
const FORMAT_LABEL: Record<string, string> = {
  csv: 'CSV',
  xlsx: 'Excel',
  geojson: 'GeoJSON',
  shp: 'Shapefile',
  shp_zip: 'ZIP',
  gpkg: 'GeoPackage',
  gdb: 'GDB',
  tif: '栅格',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function geomLabel(geometry: string | null): string {
  if (geometry === 'point') return '点'
  if (geometry === 'line') return '线'
  if (geometry === 'polygon') return '面'
  if (geometry === 'table') return '表格'
  return ''
}

/** 紧凑要素数：≥1万显示「10.3万」，否则「83」。用于行内轻量 meta。 */
function formatCount(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return n.toLocaleString()
}

/* ── 目录树 ─────────────────────────────────────────────── */

type TreeNode = TreeDir | FolderDataset

interface TreeDir {
  kind: 'dir'
  name: string
  path: string
  children: TreeNode[]
}

function isDir(n: TreeNode): n is TreeDir {
  return (n as TreeDir).kind === 'dir'
}

function buildTree(datasets: FolderDataset[]): TreeNode[] {
  const root: TreeDir = { kind: 'dir', name: '', path: '', children: [] }
  for (const d of datasets) {
    const parts = d.rel_path.split('/')
    let node: TreeDir = root
    for (let i = 0; i < parts.length - 1; i++) {
      let child = node.children.find((c) => isDir(c) && c.name === parts[i]) as TreeDir | undefined
      if (!child) {
        child = { kind: 'dir', name: parts[i], path: parts.slice(0, i + 1).join('/'), children: [] }
        node.children.push(child)
      }
      node = child
    }
    node.children.push(d)
  }
  // 目录内：目录在前、数据集在后
  const sortDir = (nodes: TreeNode[]): TreeNode[] => {
    nodes.sort((a, b) => {
      const aDir = isDir(a) ? 0 : 1
      const bDir = isDir(b) ? 0 : 1
      if (aDir !== bDir) return aDir - bDir
      const an = isDir(a) ? a.name : a.name
      const bn = isDir(b) ? b.name : b.name
      return an.localeCompare(bn, 'zh')
    })
    nodes.forEach((n) => { if (isDir(n)) sortDir(n.children) })
    return nodes
  }
  return sortDir(root.children)
}

function countEnableable(nodes: TreeNode[]): number {
  // 「可启用」同时统计 enableable 与 query_only（抽稀预览也算可启用，排除 unavailable/TIF）
  return nodes.reduce((sum, n) => {
    if (!isDir(n)) {
      return sum + ((n.status === 'enableable' || n.status === 'query_only') && !n.enabled ? 1 : 0)
    }
    return sum + countEnableable(n.children)
  }, 0)
}

function countEnabled(nodes: TreeNode[]): number {
  return nodes.reduce((sum, n) => {
    if (!isDir(n)) return sum + (n.enabled ? 1 : 0)
    return sum + countEnabled(n.children)
  }, 0)
}

/** 收集目录节点下所有叶子数据集（含嵌套子目录）。 */
function collectDatasets(nodes: TreeNode[]): FolderDataset[] {
  const out: FolderDataset[] = []
  for (const n of nodes) {
    if (isDir(n)) out.push(...collectDatasets(n.children))
    else out.push(n)
  }
  return out
}

/* ── 批量启用/停用（源级与子文件夹级共用） ───────────────────── */

/** 刷新图层列表 + 真实几何缓存，让启用/停用即时反映到地图（失败不影响操作本身）。
 * 增量只拉新增图层几何，不再全量 fetchAllGeojson（避免大图层堆叠时「启用慢」）。 */
async function refreshLayersAndGeojson(): Promise<void> {
  try {
    await refreshLayersIncremental()
  } catch {
    // 刷新失败不影响操作本身
  }
}

/** 全部启用（源级或子文件夹级共用）：scope 内候选 → 后端 dry-run 计划 → 确认框 → 执行 → 刷新图层。
 * folderPath 非空时后端只启用该子目录前缀下数据集（20 上限 + 512MB 预算逻辑原样在后端）。
 * 确认框数字来自后端 dry_run 计划（planned_count/estimated_features/capped），不自行假定一定计划 20 个，
 * 最终以服务端预算为准。 */
async function runEnableAll(
  sourceId: string,
  scopeCandidates: FolderDataset[],
  onRefresh: () => void,
  setBusy: (b: boolean) => void,
  folderPath?: string,
): Promise<void> {
  const candidatesList = scopeCandidates.filter(
    (d) => (d.status === 'enableable' || d.status === 'query_only') && !d.enabled,
  )
  if (candidatesList.length === 0) return
  let plan
  try {
    plan = await enableAllDatasets(sourceId, folderPath, true) // dry-run：只拿后端算好的计划，不真正启用
  } catch {
    // dry-run 失败（后端旧版本等）：仍允许走确认，但提示最终以后端为准
  }
  if (plan && plan.planned_count === 0) {
    const why = plan.capped_by_bytes ? '超过内存预算' : plan.capped_by_limit ? '超过数量上限' : '无可用候选'
    window.alert(`全部启用未计划任何数据集（${why}），请先停用部分图层或拆分启用。`)
    return
  }
  const lines = [
    `「全部启用」候选 ${plan?.total_candidates ?? candidatesList.length} 个，后端预算后计划启用 ${plan?.planned_count ?? Math.min(candidatesList.length, 20)} 个`,
  ]
  if (plan?.capped_by_limit) lines.push('（超过 20 个只启用前 20 个）')
  if (plan?.capped_by_bytes) {
    lines.push(`（累计内存预算约 ${((plan.estimated_bytes ?? 0) / 1024 / 1024).toFixed(0)}MB 截断，另有 ${plan?.omitted_count ?? 0} 个未启用）`)
  }
  lines.push(`预计实际载入约 ${(plan?.estimated_features ?? 0).toLocaleString()} 要素。`)
  lines.push('最终启用数量以服务端预算为准。\n\n确定继续？')
  if (!window.confirm(lines.join('\n'))) return
  setBusy(true)
  try {
    const r = await enableAllDatasets(sourceId, folderPath, false)
    await refreshLayersAndGeojson()
    if (r.failed.length > 0) {
      window.alert(`已启用 ${r.enabled} 个，${r.failed.length} 个失败：${r.failed[0].error}`)
    } else if (r.capped) {
      const reason = r.capped_by_limit ? '数量上限' : '累计预算'
      window.alert(`已启用 ${r.enabled} 个（${reason}截断，另有 ${r.omitted_count} 个未启用）`)
    } else {
      window.alert(`已启用 ${r.enabled} 个数据集`)
    }
    onRefresh()
  } catch (err) {
    window.alert((err as Error).message || '全部启用失败')
  } finally {
    setBusy(false)
  }
}

/** 全部停用（源级或子文件夹级共用）：scope 内已启用 → 确认框 → API → 刷新图层。
 * folderPath 非空时后端只停用该子目录前缀下数据集。 */
async function runDisableAll(
  sourceId: string,
  scopeCandidates: FolderDataset[],
  onRefresh: () => void,
  setBusy: (b: boolean) => void,
  folderPath?: string,
): Promise<void> {
  const enabledCount = scopeCandidates.filter((d) => d.enabled).length
  if (enabledCount === 0) return
  if (!window.confirm(`「全部停用」将停用 ${enabledCount} 个已启用数据集并移除其图层。\n\n确定继续？`)) return
  setBusy(true)
  try {
    const r = await disableAllDatasets(sourceId, folderPath)
    await refreshLayersAndGeojson()
    window.alert(`已停用 ${r.disabled} 个数据集`)
    onRefresh()
  } catch (err) {
    window.alert((err as Error).message || '全部停用失败')
  } finally {
    setBusy(false)
  }
}

/* ── 数据集行（叶子） ────────────────────────────────────── */

function DatasetRow({
  sourceId,
  dataset,
  depth,
  onRefresh,
}: {
  sourceId: string
  dataset: FolderDataset
  depth: number
  onRefresh: () => void
}) {
  const [open, setOpen] = useState(false)
  const [enabling, setEnabling] = useState(false)

  const handleEnable = async () => {
    if (enabling) return
    setEnabling(true)
    try {
      await enableDataset(sourceId, dataset.dataset_id)
      // 启用后刷新图层 + 真实几何，让新图层即时上地图（增量只拉新增图层）
      await refreshLayersAndGeojson()
      onRefresh()
    } catch (err) {
      window.alert((err as Error).message || '启用失败')
    } finally {
      setEnabling(false)
    }
  }

  const handleDisable = async () => {
    if (enabling) return
    setEnabling(true)
    try {
      await disableDataset(sourceId, dataset.dataset_id)
      // 停用后刷新图层 + 真实几何，让图层即时从地图消失（增量只拉新增图层）
      await refreshLayersAndGeojson()
      onRefresh()
    } catch (err) {
      window.alert((err as Error).message || '停用失败')
    } finally {
      setEnabling(false)
    }
  }

  return (
    <div className="folder-dataset">
      <div className="project-file" style={{ paddingLeft: 8 + depth * 14 }}>
        <button
          type="button"
          className="project-file__toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          <span className="project-file__name" title={dataset.name}>
            {dataset.name}
          </span>
          <span className="project-file__meta">
            <span className="project-file__size">
              {FORMAT_LABEL[dataset.format] ?? dataset.format}
            </span>
            {dataset.geometry && (
              <span className="project-file__size">{geomLabel(dataset.geometry)}</span>
            )}
            {dataset.feature_count != null && dataset.feature_count !== undefined && (
              <span className="project-file__size">{formatCount(dataset.feature_count)}</span>
            )}
          </span>
        </button>
        <span className="project-file__actions">
          {dataset.enabled ? (
            <button
              type="button"
              className="button button--icon button--icon--disable"
              disabled={enabling}
              title={`已启用为图层 ${dataset.layer_id}。点击停用（移除其图层）`}
              aria-label="停用该数据集"
              onClick={(event) => {
                event.stopPropagation()
                void handleDisable()
              }}
            >
              <CircleOff aria-hidden="true" />
            </button>
          ) : dataset.status === 'enableable' || dataset.status === 'query_only' ? (
            <button
              type="button"
              className="button button--icon button--icon--enable"
              disabled={enabling}
              title={
                dataset.status === 'query_only'
                  ? '数据较大，启用时抽稀；默认不预览，勾选图层眼睛即载入'
                  : `启用「${dataset.name}」`
              }
              aria-label="启用该数据集"
              onClick={(event) => {
                event.stopPropagation()
                void handleEnable()
              }}
            >
              <PlayCircle aria-hidden="true" />
            </button>
          ) : (
            <span
              className={STATUS_BADGE[dataset.status] ?? 'badge badge--neutral'}
              title={dataset.reason ?? undefined}
            >
              {STATUS_LABEL[dataset.status] ?? dataset.status}
            </span>
          )}
        </span>
      </div>
      {open && (
        <div className="folder-dataset-detail">
          <p className="folder-dataset-detail__path">{dataset.rel_path}</p>
          <p className="folder-dataset-detail__line">
            大小 {formatSize(dataset.size)}
            {dataset.crs ? ` · 坐标系 ${dataset.crs}` : ''}
          </p>
          {dataset.coord_columns && (
            <p className="folder-dataset-detail__line">
              坐标列：{dataset.coord_columns.join(' / ')}
            </p>
          )}
          {dataset.feature_count !== null && dataset.feature_count !== undefined && (
            <p className="folder-dataset-detail__line">
              {dataset.feature_count.toLocaleString()} 要素/行
            </p>
          )}
          {dataset.fields.length > 0 && (
            <p className="folder-dataset-detail__line" title={dataset.fields.map((f) => f.name).join('、')}>
              字段：{dataset.fields.slice(0, 12).map((f) => f.name).join('、')}
              {dataset.fields.length > 12 ? `（等 ${dataset.fields.length} 个）` : ''}
            </p>
          )}
          {dataset.warnings.map((w) => (
            <p key={w} className="folder-dataset-detail__warn">
              ⚠ {w}
            </p>
          ))}
          {dataset.reason && (
            <p className="folder-dataset-detail__reason">{dataset.reason}</p>
          )}
          {dataset.raster && (
            <div className="folder-dataset-detail__raster">
              {dataset.raster.preview_image && (
                <img
                  src={dataset.raster.preview_image}
                  alt={`${dataset.name} 缩略图`}
                  className="folder-dataset-detail__thumb"
                />
              )}
              <p className="folder-dataset-detail__line">
                波段：{dataset.raster.bands} · {dataset.raster.width}×{dataset.raster.height} ·{' '}
                {dataset.raster.dtype ?? '未知'}
              </p>
              {dataset.raster.res_x != null && (
                <p className="folder-dataset-detail__line">
                  分辨率：{dataset.raster.res_x} × {dataset.raster.res_y}
                </p>
              )}
              {dataset.raster.crs && (
                <p className="folder-dataset-detail__line">CRS：{dataset.raster.crs}</p>
              )}
              {dataset.raster.bounds && (
                <p className="folder-dataset-detail__line">
                  范围：{dataset.raster.bounds.map((b) => b.toFixed(4)).join(', ')}
                </p>
              )}
              {dataset.raster.preview_note && (
                <p className="folder-dataset-detail__warn">⚠ {dataset.raster.preview_note}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ── 目录节点 ────────────────────────────────────────────── */

function DirNode({
  node,
  depth,
  sourceId,
  onRefresh,
}: {
  node: TreeDir
  depth: number
  sourceId: string
  onRefresh: () => void
}) {
  const [expanded, setExpanded] = useState(depth === 0)
  const [busy, setBusy] = useState(false)
  const ready = countEnableable(node.children)
  const enabledCount = countEnabled(node.children)
  const datasets = collectDatasets(node.children)
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
          <span className="folder-tree-dir__count">{node.children.length} 项</span>
          {ready > 0 && <span className="folder-tree-dir__ready">{ready} 可启用</span>}
        </button>
        <span className="folder-tree-dir__actions">
          <button
            type="button"
            className="button button--icon button--icon--enable"
            disabled={busy || ready === 0}
            title={ready === 0 ? '该子文件夹下暂无可以启用的数据' : '启用该子文件夹下全部可启用数据集（最多 20 个）'}
            aria-label="全部启用（该子文件夹）"
            onClick={() => void runEnableAll(sourceId, datasets, onRefresh, setBusy, node.path)}
          >
            <PlayCircle aria-hidden="true" />
          </button>
          <button
            type="button"
            className="button button--icon button--icon--disable"
            disabled={busy || enabledCount === 0}
            title={enabledCount === 0 ? '该子文件夹下暂无已启用的图层' : '停用该子文件夹下全部已启用图层'}
            aria-label="全部停用（该子文件夹）"
            onClick={() => void runDisableAll(sourceId, datasets, onRefresh, setBusy, node.path)}
          >
            <CircleOff aria-hidden="true" />
          </button>
        </span>
      </div>
      {expanded &&
        node.children.map((child) =>
          isDir(child) ? (
            <DirNode
              key={child.path}
              node={child}
              depth={depth + 1}
              sourceId={sourceId}
              onRefresh={onRefresh}
            />
          ) : (
            <DatasetRow
              key={child.dataset_id}
              sourceId={sourceId}
              dataset={child}
              depth={depth + 1}
              onRefresh={onRefresh}
            />
          ),
        )}
    </div>
  )
}

/* ── 数据源卡片 ──────────────────────────────────────────── */

function SourceCard({
  source,
  onRefresh,
}: {
  source: FolderSource
  onRefresh: () => void
}) {
  const [expanded, setExpanded] = useState(true)
  const [busy, setBusy] = useState(false)
  const tree = buildTree(source.datasets)

  const handleRescan = async () => {
    setBusy(true)
    try {
      await rescanDataFolder(source.source_id)
      onRefresh()
    } catch (err) {
      window.alert((err as Error).message || '重新扫描失败')
    } finally {
      setBusy(false)
    }
  }

  const handleUnlink = async () => {
    if (!window.confirm(`取消链接「${source.alias}」？\n只移除目录快照，不影响原始数据。`)) return
    setBusy(true)
    try {
      await unlinkDataFolder(source.source_id)
      onRefresh()
    } catch (err) {
      window.alert((err as Error).message || '取消链接失败')
      setBusy(false)
    }
  }

  const handleEnableAll = () =>
    void runEnableAll(source.source_id, source.datasets, onRefresh, setBusy)
  const handleDisableAll = () =>
    void runDisableAll(source.source_id, source.datasets, onRefresh, setBusy)

  return (
    <div className="layer-group layer-group--user">
      <div
        className="layer-group__header layer-group__header--button"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}
      >
        <button
          type="button"
          className="layer-group__header-toggle"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          <FolderOpen aria-hidden="true" />
          <span className="layer-group__name">{source.alias}</span>
          <span className="layer-group__count">{source.datasets.length} 项数据</span>
        </button>
        <span style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
          <button
            type="button"
            className="button button--icon button--icon--enable"
            disabled={busy}
            title="全部启用（最多 20 个）"
            aria-label="全部启用"
            onClick={() => void handleEnableAll()}
          >
            <PlayCircle aria-hidden="true" />
          </button>
          <button
            type="button"
            className="button button--icon button--icon--disable"
            disabled={busy}
            title="全部停用（移除所有已启用图层）"
            aria-label="全部停用"
            onClick={() => void handleDisableAll()}
          >
            <CircleOff aria-hidden="true" />
          </button>
        </span>
      </div>
      {expanded && (
        <div className="layer-group__body">
          <p className="panel-hint panel-hint--compact" title={source.root_path}>
            {source.root_path}
          </p>
          {source.datasets.length === 0 ? (
            <p className="empty-state__hint">目录为空或未发现可识别数据。</p>
          ) : (
            tree.map((node) =>
              isDir(node) ? (
                <DirNode
                  key={node.path}
                  node={node}
                  depth={0}
                  sourceId={source.source_id}
                  onRefresh={onRefresh}
                />
              ) : (
                <DatasetRow
                  key={node.dataset_id}
                  sourceId={source.source_id}
                  dataset={node}
                  depth={0}
                  onRefresh={onRefresh}
                />
              ),
            )
          )}
          <div className="folder-source-actions">
            <button
              type="button"
              className="button button--ghost button--sm"
              disabled={busy}
              onClick={() => void handleRescan()}
            >
              <RefreshCw aria-hidden="true" />
              重新扫描
            </button>
            <button
              type="button"
              className="button button--ghost button--sm"
              disabled={busy}
              onClick={() => void handleUnlink()}
            >
              <Trash2 aria-hidden="true" />
              取消链接
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ── 分区主组件 ──────────────────────────────────────────── */

export function FolderSourcesSection() {
  const [sources, setSources] = useState<FolderSource[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const openUploadModal = useAppStore((s) => s.openUploadModal)
  const uploadModalOpen = useAppStore((s) => s.uploadModalOpen)
  const [downloadPath, setDownloadPath] = useState<string | null>(null)
  const [downloadModalOpen, setDownloadModalOpen] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const bumpFolderSourcesTick = useAppStore((s) => s.bumpFolderSourcesTick)

  const refresh = async (opts?: { silent?: boolean }) => {
    // silent（交互后静默刷新）：不切 loading 分支，保留已展开的目录树与操作按钮，
    // 避免启用/停用/重扫后整棵目录树收起。初次加载（无 opts）才显示 loading 空态。
    if (!opts?.silent) {
      setLoading(true)
      setError(null)
    }
    try {
      const data = await fetchDataSources()
      setSources(data.sources)
      // 数据源列表变化 → 通知「文件路径视图」重新拉取 sources（新增/取消链接/下载目录保存后归组更新）
      bumpFolderSourcesTick()
    } catch (err) {
      setError((err as Error).message || '加载数据源失败')
    } finally {
      if (!opts?.silent) setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    void fetchDownloadFolder()
      .then((d) => setDownloadPath(d.path))
      .catch(() => setDownloadPath(null))
  }, [])

  // 统一「添加数据」弹窗关闭后刷新数据源（链接目录可能新增了源）
  const prevOpen = useRef(uploadModalOpen)
  useEffect(() => {
    if (prevOpen.current && !uploadModalOpen) void refresh()
    prevOpen.current = uploadModalOpen
  }, [uploadModalOpen])

  return (
    <section className="folder-sources">
      <button
        type="button"
        className="layer-group__header layer-group__header--button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        <FolderOpen aria-hidden="true" />
        <span className="layer-group__name">本地数据目录</span>
        <span className="layer-group__count">{loading ? '…' : `${sources.length} 个目录`}</span>
      </button>
      {expanded && (
        <div className="layer-group__body">
          <div className="folder-source-tools">
            <button
              type="button"
              className="button button--secondary button--sm"
              onClick={() => openUploadModal(undefined, 'link')}
            >
              <Link2 aria-hidden="true" />
              链接目录
            </button>
            <button
              type="button"
              className="button button--ghost button--sm"
              onClick={() => setDownloadModalOpen(true)}
              title={downloadPath ?? '设置联网抓取数据的存放位置'}
            >
              <Download aria-hidden="true" />
              {downloadPath ? '数据下载目录' : '设置数据下载目录'}
            </button>
          </div>
          {downloadPath && (
            <p className="panel-hint panel-hint--compact" title={downloadPath}>
              数据下载目录：{downloadPath}
            </p>
          )}
          {loading ? (
            <p className="empty-state__hint">加载中…</p>
          ) : error ? (
            <p className="panel-hint panel-hint--compact">{error}</p>
          ) : sources.length === 0 ? (
            <p className="empty-state__hint">
              尚未链接任何本地数据目录。点击「链接目录」扫描本机数据，仅只读发现，不会修改原始文件。
            </p>
          ) : (
            sources.map((source) => (
              <SourceCard
                key={source.source_id}
                source={source}
                onRefresh={() => void refresh({ silent: true })}
              />
            ))
          )}
        </div>
      )}
      {downloadModalOpen && (
        <DownloadFolderModal
          onClose={() => setDownloadModalOpen(false)}
          onSaved={() => {
            void refresh()
            void fetchDownloadFolder().then((d) => setDownloadPath(d.path)).catch(() => {})
          }}
        />
      )}
    </section>
  )
}
