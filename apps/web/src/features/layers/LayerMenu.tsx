import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  Copy,
  Eye,
  FolderTree,
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
}

/** 图层操作菜单，参考 CARTO Builder，全部中文化 */
export function LayerMenu({ onClose, onAction, readonly = false, editable = false }: LayerMenuProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [up, setUp] = useState(false)

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [onClose])

  // 底部行菜单朝上翻转：测量菜单高度 vs 最近滚动容器的可视区。
  // 图层列表是 overflow-y:auto 的滚动容器，菜单是其 DOM 后代会被裁剪，
  // 下方放不下且上方空间更大时整体朝上弹（layer-menu--up）。
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    let scroller: HTMLElement | null = el.parentElement
    while (scroller) {
      const oy = getComputedStyle(scroller).overflowY
      if (oy === 'auto' || oy === 'scroll' || oy === 'overlay') break
      scroller = scroller.parentElement
    }
    const bounds = scroller ? scroller.getBoundingClientRect() : { top: 0, bottom: window.innerHeight }
    const rect = el.getBoundingClientRect()
    const spaceBelow = bounds.bottom - rect.top
    const spaceAbove = rect.bottom - bounds.top
    setUp(spaceBelow < rect.height && spaceAbove > spaceBelow)
  }, [])

  return (
    <div className={up ? 'layer-menu layer-menu--up' : 'layer-menu'} ref={ref} role="menu">
      {actions
        .filter(({ action }) => {
          if (readonly && action === 'delete') return false // 只读平台数据不可删
          if (action === 'edit' && !editable) return false // 「编辑顶点」仅对手绘图层显示
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
