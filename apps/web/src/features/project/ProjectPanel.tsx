import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileJson,
  FolderOpen,
  MessageSquarePlus,
  MoreVertical,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import type { ConversationMeta } from '../../store/useAppStore'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import { TextInputModal } from '../layers/TextInputModal'
import {
  deleteLayer as apiDeleteLayer,
  fetchAllGeojson,
  fetchLayers,
  openProjectFolder,
  renameProject,
  toLayer,
} from '../../lib/api'
import { setGeojsonCache } from '../map/mapLayers'
import type { ProjectFile, ProjectInfo } from '../../lib/api'

/** 可折叠分区：标题行点击展开/收起（全部项目 / 数据文件共用）。默认展开。 */
function CollapseSection({
  title,
  count,
  actions,
  children,
}: {
  title: string
  count?: number
  actions?: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(true)
  return (
    <section className="project-section">
      <div className="project-section__head">
        <button
          type="button"
          className="project-section__toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          <span className="project-section__title">{title}</span>
          {count !== undefined && <span className="project-section__count">{count}</span>}
        </button>
        {actions && <div className="project-section__actions">{actions}</div>}
      </div>
      {open && <div className="project-section__body">{children}</div>}
    </section>
  )
}

interface KebabItem {
  key: string
  label: string
  icon?: ReactNode
  danger?: boolean
  disabled?: boolean
}

/** 三点（kebab）菜单：复用 layer-menu 样式；点击外部/选择后关闭。 */
function KebabMenu({
  items,
  onSelect,
  label = '更多操作',
}: {
  items: KebabItem[]
  onSelect: (key: string) => void
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [open])
  return (
    <div className="kebab" ref={ref} onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="icon-button icon-button--tiny"
        aria-label={label}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((v) => !v)
        }}
      >
        <MoreVertical aria-hidden="true" />
      </button>
      {open && (
        <div className="layer-menu" role="menu">
          {items.map((item) => (
            <button
              type="button"
              role="menuitem"
              key={item.key}
              disabled={item.disabled}
              className={item.danger ? 'layer-menu__item layer-menu__item--danger' : 'layer-menu__item'}
              onClick={(event) => {
                event.stopPropagation()
                setOpen(false)
                onSelect(item.key)
              }}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  return iso ? iso.slice(0, 16).replace('T', ' ') : '—'
}

function FileRow({ file, onDelete }: { file: ProjectFile; onDelete: (file: ProjectFile) => void }) {
  return (
    <div className="project-file">
      <FileJson className="project-file__icon" aria-hidden="true" />
      <span className="project-file__name" title={file.name}>
        {file.name}
      </span>
      <span className="project-file__meta">
        {file.feature_count !== undefined && file.feature_count > 0 && (
          <span className="project-file__count">{file.feature_count} 要素</span>
        )}
        <span className="project-file__size">{formatSize(file.size)}</span>
      </span>
      <span className={file.on_map ? 'badge badge--success' : 'badge badge--neutral'}>
        {file.on_map ? '已上地图' : '未上地图'}
      </span>
      {file.layer_id && file.on_map && (
        <button
          type="button"
          className="icon-button icon-button--tiny project-file__delete"
          aria-label={`删除图层 ${file.layer_id}`}
          title="删除图层"
          onClick={(event) => {
            event.stopPropagation()
            onDelete(file)
          }}
        >
          <Trash2 aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

export function ProjectPanel() {
  const projects = useAppStore((s) => s.projects)
  const current = useAppStore((s) => s.currentProject)
  const detail = useAppStore((s) => s.projectDetail)
  const loading = useAppStore((s) => s.projectLoading)
  const conversations = useAppStore((s) => s.conversations)
  const activeConversationId = useAppStore((s) => s.activeConversationId)
  const refreshProjects = useAppStore((s) => s.refreshProjects)
  const createProject = useAppStore((s) => s.createProject)
  const deleteProject = useAppStore((s) => s.deleteProject)
  const switchProject = useAppStore((s) => s.switchProject)
  const openProjectDetail = useAppStore((s) => s.openProjectDetail)
  const newConversation = useAppStore((s) => s.newConversation)
  const switchConversation = useAppStore((s) => s.switchConversation)
  const renameConversation = useAppStore((s) => s.renameConversation)
  const pinConversation = useAppStore((s) => s.pinConversation)
  const deleteConversation = useAppStore((s) => s.deleteConversation)
  const openUploadModal = useAppStore((s) => s.openUploadModal)

  const [createOpen, setCreateOpen] = useState(false)
  const [renameProjectState, setRenameProjectState] = useState<ProjectInfo | null>(null)
  const [renameConv, setRenameConv] = useState<ConversationMeta | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // 挂载时拉一次项目列表
  useEffect(() => {
    void refreshProjects()
  }, [refreshProjects])

  // 当前项目变化时刷新详情（文件清单）；首次有 current 时默认展开它
  useEffect(() => {
    if (current?.id) {
      void openProjectDetail(current.id)
      setExpandedId((prev) => prev ?? current.id)
    }
  }, [current?.id, openProjectDetail])

  const handleSwitch = (id: string) => {
    if (id === current?.id) return
    void switchProject(id)
  }

  const handleDeleteProject = (project: ProjectInfo) => {
    const name = project.name || project.id
    if (!window.confirm(`删除项目「${name}」？项目的数据文件与图层将一并移除。`)) return
    void deleteProject(project.id)
  }

  const handleProjectMenu = async (key: string, project: ProjectInfo) => {
    if (key === 'pin') {
      try {
        await renameProject(project.id, { pinned: !project.pinned_at })
        await refreshProjects()
      } catch {
        /* 后端失败：保持原状 */
      }
    } else if (key === 'rename') {
      setRenameProjectState(project)
    } else if (key === 'delete') {
      handleDeleteProject(project)
    } else if (key === 'open-folder') {
      try {
        const res = await openProjectFolder(project.id)
        if (!res.opened) window.alert(`已定位到项目目录：\n${res.path}`)
      } catch (error) {
        window.alert(`打开失败：${(error as Error).message}`)
      }
    }
  }

  const handleNewConversation = async (project: ProjectInfo) => {
    if (current?.id !== project.id) await switchProject(project.id)
    newConversation()
  }

  const handleConversationMenu = (key: string, conv: ConversationMeta) => {
    if (key === 'pin') {
      pinConversation(conv.id)
    } else if (key === 'rename') {
      setRenameConv(conv)
    } else if (key === 'delete') {
      if (!window.confirm(`删除会话「${conv.title}」？会话消息将一并移除。`)) return
      deleteConversation(conv.id)
    }
  }

  const handleDeleteFile = async (file: ProjectFile) => {
    if (!file.layer_id) return
    if (!window.confirm(`删除文件「${file.name}」对应的图层？`)) return
    try {
      await apiDeleteLayer(file.layer_id)
    } catch (error) {
      window.alert(`删除失败：${(error as Error).message}`)
      return
    }
    if (current?.id) void openProjectDetail(current.id)
    try {
      const [fresh, geojson] = await Promise.all([fetchLayers(), fetchAllGeojson()])
      useAppStore.setState({ layers: fresh.map(toLayer) })
      setGeojsonCache(geojson)
    } catch {
      // 刷新失败保持现状，删除本身已成功
    }
  }

  // 会话排序：置顶优先，其次最近更新
  const projectConversations = (projectId: string) =>
    conversations
      .filter((c) => c.projectId === projectId)
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt.localeCompare(a.updatedAt))

  return (
    <div className="panel-content project-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">项目</h2>
        <div className="panel-header__actions">
          <button
            type="button"
            className="button button--secondary button--sm"
            onClick={() => setCreateOpen(true)}
          >
            <Plus aria-hidden="true" />
            新建
          </button>
          <PanelCollapseButton />
        </div>
      </header>

      <div className="scroll-area">
        {loading && <p className="panel-hint">加载中…</p>}

        {/* 当前项目信息卡 */}
        {current && (
          <section className="project-card">
            <div className="project-card__head">
              <FolderOpen className="project-card__icon" aria-hidden="true" />
              <div className="project-card__titles">
                <p className="project-card__name">{current.name}</p>
                <p className="project-card__meta">{formatDate(current.created_at ?? '')}</p>
              </div>
              <span className="badge badge--success">当前</span>
            </div>
            {current.description && <p className="project-card__desc">{current.description}</p>}
            <div className="project-card__stats">
              <div className="project-card__stat">
                <strong>{(detail ?? current).layer_count ?? 0}</strong>
                <span>图层</span>
              </div>
              <div className="project-card__stat">
                <strong>{detail?.files.length ?? 0}</strong>
                <span>数据文件</span>
              </div>
            </div>
          </section>
        )}

        {/* 项目 → 会话树 */}
        <CollapseSection title="全部项目" count={projects.length}>
          {projects.length === 0 && (
            <div className="empty-state empty-state--compact">
              <p className="empty-state__hint">还没有项目，点「新建」开始。</p>
            </div>
          )}
          <div className="project-list">
            {projects.map((project) => {
              const convos = projectConversations(project.id)
              const expanded = expandedId === project.id
              const isDefault = project.id === 'default'
              return (
                <div key={project.id} className="project-tree">
                  <div
                    className={
                      project.id === current?.id ? 'project-row is-current' : 'project-row'
                    }
                    onClick={() => handleSwitch(project.id)}
                  >
                    <button
                      type="button"
                      className="project-row__chevron"
                      aria-label={expanded ? '收起会话' : '展开会话'}
                      aria-expanded={expanded}
                      onClick={(event) => {
                        event.stopPropagation()
                        setExpandedId(expanded ? null : project.id)
                      }}
                    >
                      {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
                    </button>
                    <span className="project-row__name">
                      {project.name}
                      {isDefault && <span className="project-row__tag">内置</span>}
                    </span>
                    <span className="project-row__count">{project.layer_count} 图层</span>
                    <KebabMenu
                      label={`项目 ${project.name} 菜单`}
                      items={[
                        {
                          key: 'pin',
                          label: project.pinned_at ? '取消置顶' : '置顶',
                          icon: project.pinned_at ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />,
                        },
                        { key: 'rename', label: '重命名', icon: <Pencil aria-hidden="true" /> },
                        {
                          key: 'open-folder',
                          label: '在资源管理器打开',
                          icon: <ExternalLink aria-hidden="true" />,
                        },
                        {
                          key: 'delete',
                          label: '删除',
                          icon: <Trash2 aria-hidden="true" />,
                          danger: true,
                          disabled: isDefault,
                        },
                      ]}
                      onSelect={(key) => void handleProjectMenu(key, project)}
                    />
                  </div>
                  {expanded && (
                    <div className="project-conversations">
                      {convos.length === 0 && (
                        <p className="panel-hint panel-hint--compact">还没有会话，点「新会话」开始。</p>
                      )}
                      {convos.map((conv) => (
                        <div
                          key={conv.id}
                          className={
                            conv.id === activeConversationId
                              ? 'conversation-row is-current'
                              : 'conversation-row'
                          }
                          onClick={() => switchConversation(conv.id)}
                        >
                          <span className="conversation-row__title" title={conv.title}>
                            {conv.title}
                          </span>
                          <span className="conversation-row__time">{formatDate(conv.updatedAt)}</span>
                          <KebabMenu
                            label={`会话 ${conv.title} 菜单`}
                            items={[
                              {
                                key: 'pin',
                                label: conv.pinned ? '取消置顶' : '置顶',
                                icon: conv.pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />,
                              },
                              { key: 'rename', label: '重命名', icon: <Pencil aria-hidden="true" /> },
                              {
                                key: 'delete',
                                label: '删除',
                                icon: <Trash2 aria-hidden="true" />,
                                danger: true,
                              },
                            ]}
                            onSelect={(key) => handleConversationMenu(key, conv)}
                          />
                        </div>
                      ))}
                      <button
                        type="button"
                        className="project-row__new-conv"
                        onClick={() => void handleNewConversation(project)}
                      >
                        <MessageSquarePlus aria-hidden="true" />
                        新会话
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </CollapseSection>

        {/* 数据文件清单 */}
        <CollapseSection
          title="数据文件"
          count={detail?.files.length ?? 0}
          actions={
            current && (
              <button
                type="button"
                className="icon-button icon-button--tiny"
                aria-label="上传数据文件"
                title="上传"
                onClick={() => openUploadModal()}
              >
                <UploadCloud aria-hidden="true" />
              </button>
            )
          }
        >
          {!current && <p className="panel-hint">请先新建或选择一个项目。</p>}
          {current && (!detail || detail.files.length === 0) && (
            <div className="empty-state empty-state--compact">
              <p className="empty-state__hint">
                本项目还没有数据文件。让 Agent 下载数据或上传文件后，会出现在这里。
              </p>
            </div>
          )}
          {detail && detail.files.length > 0 && (
            <div className="project-files">
              {detail.files.map((file) => (
                <FileRow key={file.name} file={file} onDelete={(f) => void handleDeleteFile(f)} />
              ))}
            </div>
          )}
        </CollapseSection>
      </div>

      {createOpen && (
        <TextInputModal
          title="新建项目"
          desc="每个项目是独立的研究厨房：自己的数据文件、图层与会话。"
          placeholder="项目名称，如：宝安社区健康研究"
          confirmLabel="创建"
          onConfirm={(name) => {
            void createProject(name)
            setCreateOpen(false)
          }}
          onClose={() => setCreateOpen(false)}
        />
      )}

      {renameProjectState && (
        <TextInputModal
          title="重命名项目"
          desc="修改项目名称（目录结构不变）。"
          initialValue={renameProjectState.name}
          placeholder="项目名称"
          confirmLabel="保存"
          onConfirm={(name) => {
            void renameProject(renameProjectState.id, { name })
              .then(() => refreshProjects())
              .catch(() => {
                /* 后端失败：保持原状 */
              })
          }}
          onClose={() => setRenameProjectState(null)}
        />
      )}

      {renameConv && (
        <TextInputModal
          title="重命名会话"
          desc="修改会话标题。"
          initialValue={renameConv.title}
          placeholder="会话标题"
          confirmLabel="保存"
          onConfirm={(title) => renameConversation(renameConv.id, title)}
          onClose={() => setRenameConv(null)}
        />
      )}
    </div>
  )
}
