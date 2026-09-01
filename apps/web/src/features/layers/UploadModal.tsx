import { useRef, useState } from 'react'
import { FolderPlus, FolderSearch, UploadCloud, X } from 'lucide-react'
import { refreshLayersIncremental, useAppStore } from '../../store/useAppStore'
import { browseFolder, linkDataFolder, uploadLayer } from '../../lib/api'

const ACCEPTED = '.csv,.xlsx,.geojson,.json'

/** 统一的「添加数据」弹窗：上传文件 / 链接本地目录 两个 tab。
 *  挂载在 App 层，供「添加数据」按钮与引导案例共用；上传成功可选触发自动分析。 */
export function UploadModal() {
  const open = useAppStore((s) => s.uploadModalOpen)
  const tab = useAppStore((s) => s.uploadModalTab)
  const setTab = (t: 'upload' | 'link') => useAppStore.setState({ uploadModalTab: t })
  const pendingPrompt = useAppStore((s) => s.pendingPromptAfterUpload)
  const closeUploadModal = useAppStore((s) => s.closeUploadModal)
  const sendMessage = useAppStore((s) => s.sendMessage)

  // ── 上传文件 tab ──
  const [file, setFile] = useState<File | null>(null)
  const [layerName, setLayerName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // ── 链接目录 tab ──
  const [rootPath, setRootPath] = useState('')
  const [alias, setAlias] = useState('')

  if (!open) return null

  const reset = () => {
    setFile(null)
    setLayerName('')
    setError(null)
    setRootPath('')
    setAlias('')
    if (inputRef.current) inputRef.current.value = ''
  }

  const switchTab = (t: 'upload' | 'link') => {
    setTab(t)
    setError(null)
  }

  const handleUpload = async () => {
    if (!file || busy) return
    setBusy(true)
    setError(null)
    try {
      await uploadLayer(file, layerName.trim() || undefined)
      try {
        await refreshLayersIncremental()
      } catch {
        // 刷新失败保持现状，上传本身已成功
      }
      const promptAfter = pendingPrompt
      closeUploadModal()
      reset()
      if (promptAfter) void sendMessage(promptAfter)
    } catch (uploadError) {
      setError((uploadError as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const handleLink = async () => {
    const trimmed = rootPath.trim()
    if (!trimmed || busy) return
    setBusy(true)
    setError(null)
    try {
      await linkDataFolder(trimmed, alias.trim() || undefined)
      closeUploadModal()
      reset()
    } catch (err) {
      setError((err as Error).message || '链接失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const handleBrowse = async () => {
    setBusy(true)
    setError(null)
    try {
      const { path, cancelled } = await browseFolder()
      if (!cancelled && path) setRootPath(path)
    } catch (err) {
      setError((err as Error).message || '打开文件选择器失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={closeUploadModal}>
      <div
        className="modal modal--sm"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="添加数据"
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">添加数据</h3>
            <p className="modal__desc">
              {tab === 'upload' ? '上传 CSV / XLSX（自动识别经纬度列）或 GeoJSON' : '只读扫描本地目录，列出发现的数据'}
            </p>
          </div>
          <button type="button" className="icon-button" onClick={closeUploadModal} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="modal__tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'upload'}
            className={`modal__tab${tab === 'upload' ? ' is-active' : ''}`}
            onClick={() => switchTab('upload')}
          >
            <UploadCloud aria-hidden="true" />
            上传文件
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'link'}
            className={`modal__tab${tab === 'link' ? ' is-active' : ''}`}
            onClick={() => switchTab('link')}
          >
            <FolderPlus aria-hidden="true" />
            链接本地目录
          </button>
        </div>

        <div className="modal__body">
          {tab === 'upload' ? (
            <>
              <label
                className={`dropzone${file ? ' dropzone--has-file' : ''}`}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  const dropped = event.dataTransfer.files?.[0]
                  if (dropped) setFile(dropped)
                }}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept={ACCEPTED}
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
                <UploadCloud aria-hidden="true" />
                <p className="dropzone__main">{file ? file.name : '点击选择或拖拽文件到此处'}</p>
                <p className="dropzone__hint">
                  {file ? `${(file.size / 1024).toFixed(1)} KB` : '支持 CSV、XLSX、GeoJSON，最大 10MB'}
                </p>
              </label>

              <div className="form-field">
                <label className="form-field__label">图层名称（可选，默认用文件名）</label>
                <input
                  type="text"
                  className="form-field__input"
                  value={layerName}
                  placeholder="例如：POI 设施点位"
                  onChange={(event) => setLayerName(event.target.value)}
                />
              </div>
            </>
          ) : (
            <>
              <div className="form-field">
                <label className="form-field__label">目录路径</label>
                <div className="folder-path-row">
                  <input
                    type="text"
                    className="form-field__input"
                    value={rootPath}
                    placeholder="点击右侧「浏览文件夹」选择，或手动粘贴路径"
                    onChange={(event) => setRootPath(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void handleLink()
                    }}
                  />
                  <button
                    type="button"
                    className="button button--secondary button--sm"
                    disabled={busy}
                    onClick={() => void handleBrowse()}
                  >
                    <FolderSearch aria-hidden="true" />
                    浏览文件夹…
                  </button>
                </div>
              </div>
              <div className="form-field">
                <label className="form-field__label">数据源别名（可选，默认用目录名）</label>
                <input
                  type="text"
                  className="form-field__input"
                  value={alias}
                  placeholder="例如：乐活大道"
                  onChange={(event) => setAlias(event.target.value)}
                />
              </div>
            </>
          )}

          {error && <p className="form-error">{error}</p>}
        </div>

        <div className="modal__footer">
          <button type="button" className="button button--ghost" onClick={closeUploadModal}>
            取消
          </button>
          {tab === 'upload' ? (
            <button
              type="button"
              className="button button--primary"
              disabled={!file || busy}
              onClick={handleUpload}
            >
              {busy ? '上传中…' : '上传并加载'}
            </button>
          ) : (
            <button
              type="button"
              className="button button--primary"
              disabled={!rootPath.trim() || busy}
              onClick={() => void handleLink()}
            >
              <FolderPlus aria-hidden="true" />
              {busy ? '扫描中…' : '链接并扫描'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
