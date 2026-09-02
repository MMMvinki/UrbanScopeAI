import type { LegendGroup } from './analysisMapper'
import { formatArea, formatInt } from './analysisMapper'

/**
 * 分析板块图例：读 mapper 输出的分型数据，画「色块 + 分类名 + 格数 + 面积 + 商业点数」表。
 * 与真地图上的分类配色图例（LayerLegend）职责分开：这里只画 stats 里的分类统计，
 * 真地图图例由现有 LayerLegend 自动渲染。
 */
export function AnalysisLegend({ groups }: { groups: LegendGroup[] }) {
  const showPoints = groups.some((g) => g.rows.some((r) => r.points != null))
  const showArea = groups.some((g) => g.rows.some((r) => r.areaKm2 != null))
  return (
    <div className="analysis-legend">
      {groups.map((group) => (
        <div className="analysis-legend__group" key={group.title}>
          <div className="analysis-legend__title">{group.title}</div>
          <table className="analysis-legend__table">
            <thead>
              <tr>
                <th aria-label="色块" />
                <th>类型</th>
                <th className="analysis-legend__num">格数</th>
                {showArea ? <th className="analysis-legend__num">面积</th> : null}
                {showPoints ? <th className="analysis-legend__num">商业点</th> : null}
              </tr>
            </thead>
            <tbody>
              {group.rows.map((row) => (
                <tr key={row.code}>
                  <td>
                    <span
                      className="analysis-legend__swatch"
                      style={{ backgroundColor: row.color }}
                      aria-hidden="true"
                    />
                  </td>
                  <td>{row.label}</td>
                  <td className="analysis-legend__num">{row.cells != null ? formatInt(row.cells) : '—'}</td>
                  {showArea ? (
                    <td className="analysis-legend__num">
                      {row.areaKm2 != null ? formatArea(row.areaKm2) : '—'}
                    </td>
                  ) : null}
                  {showPoints ? (
                    <td className="analysis-legend__num">
                      {row.points != null ? formatInt(row.points) : '—'}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {group.note ? <div className="analysis-legend__note">{group.note}</div> : null}
        </div>
      ))}
    </div>
  )
}
