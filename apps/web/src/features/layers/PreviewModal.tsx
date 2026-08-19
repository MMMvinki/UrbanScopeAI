import { useEffect, useMemo, useState } from 'react'
import { Search, X } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { ensureLayerGeojson, getLayerGeojson, hasLayerGeojson } from '../map/mapLayers'

interface PreviewModalProps {
  layerId: string
  onClose: () => void
}

const MAX_ROWS = 100

/** 属性表（Sprint 2 S2-3）：字段列 ← geojson properties，前 100 行 + 行内搜索过滤。
 *  数据直接读 geojson 缓存，无需新后端接口。平台数据 lazy 图层：先按需拉取几何。 */
export function PreviewModal({ layerId, onClose }: PreviewModalProps) {
  const layer = useAppStore((s) => s.layers.find((item) => item.id === layerId))
  const [query, setQuery] = useState('')
  const [tick, setTick] = useState(0)

  // 平台数据惰性图层：打开属性表先按需拉几何，就绪后 tick 驱动重算
  useEffect(() => {
    let active = true
    if (layer?.lazy && !hasLayerGeojson(layerId)) {
      void ensureLayerGeojson(layerId).then((ok) => {
        if (ok && active) setTick((t) => t + 1)
      })
    }
    return () => {
      active = false
    }
  }, [layerId, layer?.lazy])

  const data = useMemo(() => {
    if (!layer) return null
    const collection = getLayerGeojson(layer)
    const features = collection.features ?? []
    const fields = Array.from(
      new Set(features.flatMap((feature) => Object.keys(feature.properties ?? {}))),
    )
    const geometryTypes = Array.from(
      new Set(features.map((feature) => feature.geometry?.type).filter(Boolean) as string[]),
    )
    return { features, fields, geometryTypes }
  }, [layer, tick])

  const rows = useMemo(() => {
    if (!data) return []
    const q = query.trim().toLowerCase()
    if (!q) return data.features
    return data.features.filter((feature) =>
      data.fields.some((field) =>
        String(feature.properties?.[field] ?? '').toLowerCase().includes(q),
      ),
    )
  }, [data, query])

  if (!layer || !data) return null
  const shown = rows.slice(0, MAX_ROWS)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--lg"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`${layer.name} 属性表`}
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">属性表</h3>
            <p className="modal__desc">
              {layer.name} · {data.features.length} 个要素 · 几何类型{' '}
              {data.geometryTypes.join(' / ')}
              {query.trim() && ` · 搜索命中 ${rows.length} 行`}
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          <div className="attr-search">
            <Search aria-hidden="true" />
            <input
              type="search"
              placeholder="搜索属性…（按任意字段过滤）"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          {data.fields.length === 0 ? (
            <p className="preview-empty">该图层没有属性字段</p>
          ) : (
            <div className="preview-table-wrap">
              <table className="preview-table">
                <thead>
                  <tr>
                    <th>#</th>
                    {data.fields.map((field) => (
                      <th key={field}>{field}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shown.map((feature, index) => (
                    <tr key={index}>
                      <td className="preview-table__idx">{index + 1}</td>
                      {data.fields.map((field) => (
                        <td key={field}>{String(feature.properties?.[field] ?? '')}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length > MAX_ROWS && (
                <p className="preview-table__note">
                  仅显示前 {MAX_ROWS} 行，共 {rows.length} 行（匹配）
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
