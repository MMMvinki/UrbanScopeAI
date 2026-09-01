import { useState } from 'react'
import { Plus, Trash2, X } from 'lucide-react'
import type { FieldSpec, GeometryType } from '../layers/layerTypes'
import {
  AREA_UNITS,
  LENGTH_UNITS,
  autoFieldKeysFor,
  computeAutoFields,
  injectAutoFields,
} from './geodesic'
import type { AutoUnitConfig } from './geodesic'

interface DrawnAttributesPanelProps {
  features: GeoJSON.Feature[]
  geometryType: GeometryType
  color: string
  onCancel: () => void
  /** 确认：回传图层名 + 已写入属性后的要素 + 字段 schema + 本次所选单位 */
  onConfirm: (input: {
    name: string
    features: GeoJSON.Feature[]
    fields: FieldSpec[]
    units: AutoUnitConfig
  }) => void
}

interface Column {
  id: string
  name: string
  type: FieldSpec['type']
}

const GEOMETRY_LABEL: Record<GeometryType, string> = {
  Point: '点',
  LineString: '线',
  Polygon: '面',
  Table: '表格',
}

const FIELD_TYPES: Array<{ value: string; label: string }> = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数值' },
  { value: 'boolean', label: '布尔' },
]

let columnSeq = 0
const makeId = () => `col_${++columnSeq}`

// 默认两列：名称 / 类型；值按列 id 存，重命名列不影响已填值。
// 模块级常量：useState 两个初始化器（columns、rows）共用，无 TDZ 问题。
const DEFAULT_COLUMNS: Column[] = [
  { id: makeId(), name: '名称', type: 'string' },
  { id: makeId(), name: '类型', type: 'string' },
]

/** 绘制完成后的要素属性面板：填图层名 + 单位设置 + 自动列（只读）+ 自定义列，确认后创建图层。 */
export function DrawnAttributesPanel({
  features,
  geometryType,
  color,
  onCancel,
  onConfirm,
}: DrawnAttributesPanelProps) {
  const [name, setName] = useState(`手绘${GEOMETRY_LABEL[geometryType]}图层`)
  const [columns, setColumns] = useState<Column[]>(() => DEFAULT_COLUMNS.map((col) => ({ ...col })))
  // 自动字段单位偏好：长度 米/千米、面积 ㎡/公顷(hm²)/km²；随选择重算自动列
  const [units, setUnits] = useState<AutoUnitConfig>({ length: 'm', area: 'm2' })
  const [rows, setRows] = useState<Array<Record<string, string>>>(() =>
    features.map((feature, index) => {
      const props = feature.properties ?? {}
      const row: Record<string, string> = {}
      for (const col of DEFAULT_COLUMNS) {
        const value = props[col.name]
        row[col.id] = value != null && typeof value !== 'object' ? String(value) : ''
      }
      const nameCol = DEFAULT_COLUMNS.find((col) => col.name === '名称')
      if (nameCol && !row[nameCol.id]) row[nameCol.id] = `要素 ${index + 1}`
      return row
    }),
  )

  // 自动列（只读）：依当前 features 几何类型 + 单位派生，随 units 变化重算
  const autoColumns = autoFieldKeysFor(features, units)

  const renameColumn = (id: string, name: string) => {
    setColumns((cols) => cols.map((col) => (col.id === id ? { ...col, name } : col)))
  }
  const setColumnType = (id: string, type: string) => {
    setColumns((cols) => cols.map((col) => (col.id === id ? { ...col, type } : col)))
  }
  const addColumn = () => {
    const id = makeId()
    const n = columns.length + 1
    setColumns((cols) => [...cols, { id, name: `字段${n}`, type: 'string' }])
    setRows((rs) => rs.map((row) => ({ ...row, [id]: '' })))
  }
  const removeColumn = (id: string) => {
    setColumns((cols) => cols.filter((col) => col.id !== id))
    setRows((rs) =>
      rs.map((row) => {
        const next = { ...row }
        delete next[id]
        return next
      }),
    )
  }
  const setCell = (rowIndex: number, colId: string, value: string) => {
    setRows((rs) => rs.map((row, i) => (i === rowIndex ? { ...row, [colId]: value } : row)))
  }

  const confirm = () => {
    // 先注入自动字段（清旧单位残留 + 按当前 units 重算），再覆盖用户自定义列（用户优先）
    const baseFeatures = injectAutoFields(features, units)
    const nextFeatures = baseFeatures.map((feature, i) => {
      const props: Record<string, unknown> = { ...feature.properties }
      for (const col of columns) {
        const value = rows[i]?.[col.id]
        if (value === undefined || value === '') continue
        props[col.name] = coerceType(value, col.type)
      }
      return { ...feature, properties: props }
    })
    const fields: FieldSpec[] = [
      ...autoColumns.map((name) => ({ name, type: 'number' })),
      ...columns.map((col) => ({ name: col.name, type: col.type })),
    ]
    onConfirm({ name: name.trim() || '手绘图层', features: nextFeatures, fields, units })
  }

  // 某要素在自动列下的只读值（如面积(公顷)）；列名不存在则空
  const autoValueFor = (feature: GeoJSON.Feature, colName: string): string => {
    const value = computeAutoFields(feature, units)[colName]
    return value != null && Number.isFinite(value) ? (value as number).toPrecision(5) : ''
  }

  return (
    <div className="map-draw-panel" role="dialog" aria-label="绘制要素属性">
      <div className="map-draw-panel__head">
        <span className="map-draw-panel__title">
          <i className="map-draw-panel__dot" style={{ background: color }} />
          绘制完成 · {features.length} 个要素
        </span>
        <button type="button" className="map-draw-panel__close" onClick={onCancel} title="取消并清空">
          <X aria-hidden="true" />
        </button>
      </div>

      <label className="map-draw-panel__field">
        <span className="map-draw-panel__label">图层名</span>
        <input
          className="map-draw-panel__input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={40}
        />
      </label>

      <div className="map-draw-panel__units">
        <label className="map-draw-panel__unit">
          <span className="map-draw-panel__label">长度单位</span>
          <select
            className="map-draw-panel__input"
            value={units.length ?? 'm'}
            onChange={(event) => setUnits((prev) => ({ ...prev, length: event.target.value as 'm' | 'km' }))}
          >
            {LENGTH_UNITS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="map-draw-panel__unit">
          <span className="map-draw-panel__label">面积单位</span>
          <select
            className="map-draw-panel__input"
            value={units.area ?? 'm2'}
            onChange={(event) => setUnits((prev) => ({ ...prev, area: event.target.value as AutoUnitConfig['area'] }))}
          >
            {AREA_UNITS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="map-draw-panel__table-wrap">
        <table className="map-draw-panel__table">
          <thead>
            <tr>
              <th className="map-draw-panel__th-index">#</th>
              {columns.map((col) => (
                <th key={col.id}>
                  <div className="map-draw-panel__col">
                    <input
                      className="map-draw-panel__input map-draw-panel__col-name"
                      value={col.name}
                      onChange={(event) => renameColumn(col.id, event.target.value)}
                      maxLength={20}
                    />
                    <select
                      className="map-draw-panel__col-type"
                      value={col.type}
                      onChange={(event) => setColumnType(col.id, event.target.value)}
                      title="字段类型"
                    >
                      {FIELD_TYPES.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="map-draw-panel__col-remove"
                      onClick={() => removeColumn(col.id)}
                      title="删除字段"
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </div>
                </th>
              ))}
              {autoColumns.length > 0 && (
                <th
                  className="map-draw-panel__th-auto"
                  colSpan={autoColumns.length}
                  title="自动字段（按几何类型自动计算，只读）"
                >
                  自动字段
                </th>
              )}
            </tr>
            {autoColumns.length > 0 && (
              <tr className="map-draw-panel__auto-head">
                <th className="map-draw-panel__th-index" />
                <th colSpan={columns.length} />
                {autoColumns.map((name) => (
                  <th key={name} className="map-draw-panel__th-auto-name">
                    {name}
                  </th>
                ))}
              </tr>
            )}
          </thead>
          <tbody>
            {features.map((feature, rowIndex) => (
              <tr key={String(feature.id ?? rowIndex)}>
                <td className="map-draw-panel__td-index">{rowIndex + 1}</td>
                {columns.map((col) => (
                  <td key={col.id}>
                    <input
                      className="map-draw-panel__input"
                      value={rows[rowIndex]?.[col.id] ?? ''}
                      onChange={(event) => setCell(rowIndex, col.id, event.target.value)}
                      placeholder="—"
                    />
                  </td>
                ))}
                {autoColumns.map((name) => (
                  <td key={name} className="map-draw-panel__td-auto">
                    <span className="map-draw-panel__auto-value">{autoValueFor(feature, name)}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button type="button" className="map-draw-panel__add-field" onClick={addColumn}>
        <Plus aria-hidden="true" />
        添加字段
      </button>

      <div className="map-draw-panel__actions">
        <button type="button" className="map-draw-panel__btn map-draw-panel__btn-cancel" onClick={onCancel}>
          取消
        </button>
        <button type="button" className="map-draw-panel__btn map-draw-panel__btn-create" onClick={confirm}>
          确认创建
        </button>
      </div>
    </div>
  )
}

/** 按字段类型把输入值 coerce 成目标类型（number/boolean），文本保持原样 */
function coerceType(value: string, type: string): unknown {
  if (type === 'number') {
    const n = Number(value)
    return Number.isFinite(n) ? n : value
  }
  if (type === 'boolean') {
    return value === 'true' || value === '是' || value === '1'
  }
  return value
}
