import { useAppStore } from '../store/useAppStore'

export function TopBar() {
  const layers = useAppStore((s) => s.layers)
  const activeLayers = layers.filter((layer) => layer.visible).length
  const projects = useAppStore((s) => s.projects)
  const currentProject = useAppStore((s) => s.currentProject)
  const switchProject = useAppStore((s) => s.switchProject)

  return (
    <header className="topbar">
      <div className="topbar__titles">
        <p className="topbar__eyebrow">UrbanScope AI</p>
        <h1 className="topbar__title">{currentProject?.name ?? '项目工作台'}</h1>
      </div>
      <div className="topbar__actions">
        {projects.length > 0 && (
          <select
            className="topbar__select"
            value={currentProject?.id ?? ''}
            aria-label="切换项目"
            onChange={(event) => {
              if (event.target.value) void switchProject(event.target.value)
            }}
          >
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        )}
        <span className="badge badge--success">
          数据就绪 · {activeLayers}/{layers.length} 图层
        </span>
      </div>
    </header>
  )
}
