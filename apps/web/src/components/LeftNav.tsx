import { Boxes, Database, FolderOpen, FolderOutput, Map, Wrench } from 'lucide-react'
import type { NavKey } from '../store/useAppStore'

// Agent 移出左栏成右锚浮窗（悬浮球入口）：导航只留功能模块 + 成果页
const navItems: { key: NavKey; label: string; icon: typeof Map }[] = [
  { key: 'project', label: '项目', icon: FolderOpen },
  { key: 'data', label: '数据', icon: Database },
  { key: 'tools', label: 'Tools', icon: Wrench },
  { key: 'skills', label: '技能', icon: Boxes },
  { key: 'results', label: '成果', icon: FolderOutput },
]

interface LeftNavProps {
  active: NavKey
  onSelect: (key: NavKey) => void
}

export function LeftNav({ active, onSelect }: LeftNavProps) {
  return (
    <aside className="left-nav" aria-label="主导航">
      <div className="left-nav__brand" aria-hidden="true">
        <Map />
      </div>
      <nav className="left-nav__list">
        {navItems.map(({ key, label, icon: Icon }) => (
          <button
            type="button"
            key={key}
            className={active === key ? 'left-nav__item is-active' : 'left-nav__item'}
            aria-current={active === key ? 'page' : undefined}
            onClick={() => onSelect(key)}
          >
            <Icon aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="left-nav__footer">
        <span className="left-nav__version">v0.1</span>
      </div>
    </aside>
  )
}
