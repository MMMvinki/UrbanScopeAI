import { useRef, useState } from 'react'
import { UploadCloud, X } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { fetchAllGeojson, fetchLayers, toLayer, uploadLayer } from '../../lib/api'
import { setGeojsonCache } from '../map/mapLayers'

const ACCEPTED = '.csv,.xlsx,.geojson,.json,.gpkg,.zip'

/** 上传图层弹窗：CSV/XLSX（自动识别经纬度列）/ GeoJSON / GPKG / Shapefile(zip) 注册为地图图层。
 *  挂载在 App 层，供「添加图层」按钮与引导案例 2 共用；成功后可选触发自动分析。
 *  说明：Shapefile 是 .shp+.dbf+.shx+.prj 多文件，浏览器只能选单个文件，故必须打包成 .zip
 *  上传（后端 ingest 会解压 zip 并读 .shp）。裸选单个 .shp 会因缺 .dbf 等而读取失败。 */
export function UploadModal() {
  const open = useAppStore((s) => s.uploadModalOpen)
  const pendingPrompt = useAppStore((s) => s.pendingPromptAfterUpload)
  const closeUploadModal = useAppStore((s) => s.closeUploadModal)
  const sendMessage = useAppStore((s) => s.sendMessage)

  const [file, setFile] = useState<File | null>(null)
  const [layerName, setLayerName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  if (!open) return null

  const reset = () => {
    setFile(null)
    setLayerName('')
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleUpload = async () => {
    if (!file || busy) return
    setBusy(true)
    setError(null)
    try {
      await uploadLayer(file, layerName.trim() || undefined)
      // 刷新图层列表与真实几何（同 sendMessage 第 4 步）
      try {
        const [fresh, geojson] = await Promise.all([fetchLayers(), fetchAllGeojson()])
        useAppStore.setState({ layers: fresh.map(toLayer) })
        setGeojsonCache(geojson)
      } catch {
        // 刷新失败保持现状，上传本身已成功
      }
      const promptAfter = pendingPrompt
      closeUploadModal()
      reset()
      if (promptAfter) {
        // 引导案例 2：上传成功后自动发起概览分析
        void sendMessage(promptAfter)
      }
    } catch (uploadError) {
      setError((uploadError as Error).message)
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
        aria-label="上传图层"
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">上传图层</h3>
            <p className="modal__desc">CSV / XLSX / GeoJSON / GPKG / Shapefile(.zip)</p>
          </div>
          <button type="button" className="icon-button" onClick={closeUploadModal} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
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
            <p className="dropzone__main">
              {file ? file.name : '点击选择或拖拽文件到此处'}
            </p>
            <p className="dropzone__hint">
              {file
                ? `${(file.size / 1024).toFixed(1)} KB`
                : '支持 CSV、XLSX、GeoJSON、GPKG、Shapefile（.zip 打包，需含 .shp/.dbf/.shx/.prj）'}
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

          {error && <p className="form-error">{error}</p>}
        </div>
        <div className="modal__footer">
          <button type="button" className="button button--ghost" onClick={closeUploadModal}>
            取消
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={!file || busy}
            onClick={handleUpload}
          >
            {busy ? '上传中…' : '上传并加载'}
          </button>
        </div>
      </div>
    </div>
  )
}
