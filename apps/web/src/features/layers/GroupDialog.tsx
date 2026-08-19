import { useState } from 'react'
import { X } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'

interface GroupDialogProps {
  layerId: string
  onClose: () => void
}

/** 分组弹窗：为当前图层创建分组 / 移动到已有分组 / 取消分组 */
export function GroupDialog({ layerId, onClose }: GroupDialogProps) {
  const layer = useAppStore((s) => s.layers.find((item) => item.id === layerId))
  const groups = useAppStore((s) => s.groups)
  const createGroup = useAppStore((s) => s.createGroup)
  const moveLayerToGroup = useAppStore((s) => s.moveLayerToGroup)
  const removeGroup = useAppStore((s) => s.removeGroup)

  const [groupName, setGroupName] = useState('')

  if (!layer) return null
  const currentGroupId = groups.find((group) => group.layerIds.includes(layerId))?.id

  const handleCreate = () => {
    const name = groupName.trim()
    if (!name) return
    createGroup(name, layerId)
    setGroupName('')
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--sm"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="图层分组"
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">图层分组</h3>
            <p className="modal__desc">{layer.name}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          <div className="form-field">
            <label className="form-field__label">新分组名称</label>
            <div className="form-field__row">
              <input
                type="text"
                className="form-field__input"
                value={groupName}
                placeholder="例如：人口数据"
                onChange={(event) => setGroupName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') handleCreate()
                }}
              />
              <button type="button" className="button button--primary" onClick={handleCreate}>
                创建并加入
              </button>
            </div>
          </div>

          <div className="modal__section">
            <h4 className="modal__section-title">移动到已有分组</h4>
            {groups.length === 0 ? (
              <p className="form-field__hint">还没有分组，先在上方创建。</p>
            ) : (
              <ul className="group-list">
                {groups.map((group) => (
                  <li key={group.id} className="group-list__item">
                    <span className="group-list__name">
                      {group.name}
                      <span className="group-list__count">{group.layerIds.length} 个图层</span>
                    </span>
                    <span className="group-list__actions">
                      {currentGroupId === group.id ? (
                        <span className="badge">当前</span>
                      ) : (
                        <button
                          type="button"
                          className="button button--ghost button--sm"
                          onClick={() => moveLayerToGroup(layerId, group.id)}
                        >
                          移入
                        </button>
                      )}
                      <button
                        type="button"
                        className="button button--ghost button--sm button--danger"
                        onClick={() => removeGroup(group.id)}
                        aria-label={`删除分组 ${group.name}`}
                      >
                        删除
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {currentGroupId && (
            <div className="modal__footer">
              <button
                type="button"
                className="button button--ghost"
                onClick={() => moveLayerToGroup(layerId, null)}
              >
                移出分组
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
