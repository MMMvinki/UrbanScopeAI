import { useState } from 'react'
import { Download, FolderSearch, X } from 'lucide-react'
import { browseFolder, setDownloadFolder } from '../../lib/api'

interface DownloadFolderModalProps {
  onClose: () => void
  onSaved: () => void
}

/** 设置下载目录弹窗：联网取数（OSM/高德/datasource）默认落盘到该本地文件夹。 */
export function DownloadFolderModal({ onClose, onSaved }: DownloadFolderModalProps) {  const [rootPath, setRootPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  const handleSave = async () => {
    const trimmed = rootPath.trim()
    if (!trimmed || busy) return
    setBusy(true)
    setError(null)
    try {
      await setDownloadFolder(trimmed)
      onSaved()
      onClose()
    } catch (err) {
      setError((err as Error).message || '设置失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--sm"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="设置数据下载目录"
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">设置数据下载目录</h3>
            <p className="modal__desc">
              这里设置在平台内联网抓取（OSM / 地图下载等）的数据存放在本机哪个文件夹；设好后会自动建为数据源并纳入「本地数据目录」。
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          <div className="form-field">
            <label className="form-field__label">数据下载目录路径</label>
            <div className="folder-path-row">
              <input
                type="text"
                className="form-field__input"
                value={rootPath}
                placeholder="点击「浏览文件夹」选择，或手动粘贴路径"
                onChange={(event) => setRootPath(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void handleSave()
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
          {error && <p className="form-error">{error}</p>}
        </div>
        <div className="modal__footer">
          <button type="button" className="button button--ghost" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={!rootPath.trim() || busy}
            onClick={() => void handleSave()}
          >
            <Download aria-hidden="true" />
            {busy ? '设置中…' : '设为数据下载目录'}
          </button>
        </div>
      </div>
    </div>
  )
}
