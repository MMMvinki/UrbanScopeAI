import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Copy,
  Eye,
  FolderTree,
  HardDrive,
  Move,
  Palette,
  Pencil,
  Rows3,
  Trash2,
  ZoomIn,
} from 'lucide-react'
import type { LayerAction } from './layerTypes'

const actions: { action: LayerAction; label: string; icon: typeof ZoomIn; destructive?: boolean }[] =
  [
    { action: 'zoom', label: '缩放至图层', icon: ZoomIn },
    { action: 'show-only', label: '仅显示此图层', icon: Eye },
    { action: 'edit', label: '编辑顶点', icon: Move },
    { action: 'style', label: '图层样式', icon: Palette },
    { action: 'duplicate', label: '复制', icon: Copy },
    { action: 'rename', label: '重命名', icon: Pencil },
    { action: 'group', label: '创建分组', icon: FolderTree },
    { action: 'data-path', label: '查看数据路径', icon: HardDrive },
    { action: 'preview', label: '属性表', icon: Rows3 },
    { action: 'delete', label: '删除', icon: Trash2, destructive: true },
  ]

interface LayerMenuProps {
  onClose: () => void
  onAction: (action: LayerAction) => void
  /** 只读平台图层：删除操作隐藏（数据内容不可删，样式/改名仍可用） */
  readonly?: boolean
  /** 手绘可编辑图层：显示「编辑顶点」，供改几何后保存 */
  editable?: boolean
  /** 表格图层（无几何）：隐藏缩放/仅显示/复制等无意义操作（复制会得到空副本） */
  table?: boolean
  /** 源数据磁盘路径：有才显示「查看数据路径」（folder/uploaded 图层定位用） */
  dataPath?: string | null
}

/** 图层操作菜单，参考 CARTO Builder，全部中文化 */
export function LayerMenu({
  onClose,
  onAction,
  readonly = false,
  editable = false,
  table = false,
  dataPath,
}: LayerMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [onClose])

  // 菜单用「视口 fixed 定位」：不再依赖 CSS 的 absolute + 父滚动容器（那会在列表滚动容器
  // overflow-y:auto 里被裁剪，导致前部菜单项（图层样式/缩放等）看不见、点不到）。
  // 以挂点（menu-wrap）rect 为基准，下方放不下则弹到上方，并 clamp 到视口内。
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const anchor = el.parentElement?.getBoundingClientRect()
    if (!anchor) return
    const menuW = Math.max(el.offsetWidth, 172)
    const menuH = el.offsetHeight
    const vw = window.innerWidth
    const vh = window.innerHeight
    const gap = 4
    let left = Math.min(Math.max(8, anchor.right - menuW), Math.max(8, vw - menuW - 8))
    let top = anchor.bottom + gap
    if (top + menuH > vh - 8) {
      top = anchor.top - menuH - gap
    }
    top = Math.max(8, Math.min(top, vh - menuH - 8))
    setPos({ left, top })
  }, [])

  return (
    <div
      className="layer-menu"
      ref={ref}
      role="menu"
      style={pos ? { position: 'fixed', left: pos.left, top: pos.top, zIndex: 90 } : undefined}
    >
      {actions
        .filter(({ action }) => {
          if (readonly && action === 'delete') return false // 只读平台数据不可删
          if (action === 'edit' && !editable) return false // 「编辑顶点」仅对手绘图层显示
          if (action === 'data-path' && !dataPath) return false // 无源路径则隐藏「查看数据路径」
          if (table && (action === 'zoom' || action === 'show-only' || action === 'duplicate')) return false // 表格无几何：缩放/仅显示/复制无意义
          return true
        })
        .map(({ action, label, icon: Icon, destructive }) => (
        <button
          type="button"
          role="menuitem"
          key={action}
          className={destructive ? 'layer-menu__item layer-menu__item--danger' : 'layer-menu__item'}
          onClick={() => onAction(action)}
        >
          <Icon aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  )
}
