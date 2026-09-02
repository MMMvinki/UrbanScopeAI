import { Locate } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import type { AnalysisSection, StatMetric } from './analysisMapper'
import { AnalysisLegend } from './AnalysisLegend'
import type { FunnelStep } from './analysisMapper'
import { formatInt } from './analysisMapper'

function MetricTile({ metric }: { metric: StatMetric }) {
  return (
    <div className="analysis-metric">
      <span className="analysis-metric__value">{metric.value}</span>
      <span className="analysis-metric__label">{metric.label}</span>
      {metric.hint ? <span className="analysis-metric__hint">{metric.hint}</span> : null}
    </div>
  )
}

function MetricRow({ metrics }: { metrics: StatMetric[] }) {
  if (!metrics || metrics.length === 0) return null
  return (
    <div className="analysis-metrics">
      {metrics.map((m) => (
        <MetricTile metric={m} key={m.label} />
      ))}
    </div>
  )
}

function Funnel({ steps }: { steps: FunnelStep[] }) {
  return (
    <div className="analysis-funnel">
      <div className="analysis-funnel__title">数据漏斗</div>
      <ol className="analysis-funnel__steps">
        {steps.map((step, i) => (
          <li className="analysis-funnel__step" key={step.label}>
            <span className="analysis-funnel__label">{step.label}</span>
            <span className="analysis-funnel__value">{formatInt(step.value)}</span>
            {i < steps.length - 1 ? <span className="analysis-funnel__arrow" aria-hidden="true">→</span> : null}
          </li>
        ))}
      </ol>
    </div>
  )
}

function EvidenceBlock({ evidence }: { evidence: string[] }) {
  if (!evidence || evidence.length === 0) return null
  return (
    <details className="analysis-evidence">
      <summary>数据与方法依据</summary>
      <ul className="analysis-evidence__list">
        {evidence.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </details>
  )
}

function Limitations({ items }: { items: string[] }) {
  if (!items || items.length === 0) return null
  return (
    <div className="analysis-limitations">
      <span className="analysis-limitations__label">限制说明</span>
      <ul className="analysis-limitations__list">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

function ReadinessMatrix({ section }: { section: AnalysisSection }) {
  const rows = section.readiness ?? []
  if (rows.length === 0) return null
  return (
    <div className="analysis-readiness">
      <div className="analysis-readiness__title">数据就绪矩阵</div>
      <table className="analysis-readiness__table">
        <thead>
          <tr>
            <th>板块</th>
            <th>状态</th>
            <th>需要的数据</th>
            <th>就绪说明</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.moduleId}>
              <td>{row.moduleLabel}</td>
              <td>
                <span className={row.status === 'ready' ? 'badge badge--success' : 'badge badge--error'}>
                  {row.status === 'ready' ? '就绪' : '阻断'}
                </span>
              </td>
              <td className="analysis-readiness__data">{row.requiredData.join('、')}</td>
              <td className="analysis-readiness__reason">{row.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * 板块卡外壳：标题 + 状态/层级徽章 + 正文 + 限制说明 + 运行编号 + 「在地图上查看」。
 * 完成态显示真实数字与图例；阻断态不显示假数字/假地图，只显示缺口、停止代码与降级建议。
 */
export function SectionCard({ section }: { section: AnalysisSection }) {
  const setLayerVisible = useAppStore((s) => s.setLayerVisible)
  const setActiveNav = useAppStore((s) => s.setActiveNav)

  const viewOnMap = () => {
    if (!section.outputLayer) return
    setLayerVisible(section.outputLayer, true)
    setActiveNav('data')
  }

  const statusBadge =
    section.status === 'blocked' ? (
      <span className="badge badge--error">因缺数据停止</span>
    ) : section.status === 'completed' ? (
      <span className="badge badge--success">完成</span>
    ) : null

  const levelBadge = section.achievedLevel ? (
    <span className="badge badge--neutral">达到 {section.achievedLevel}</span>
  ) : null

  return (
    <section id={`analysis-${section.id}`} className="analysis-card" aria-label={section.title}>
      <header className="analysis-card__head">
        <span className="analysis-card__index" aria-hidden="true">
          {section.index + 1}
        </span>
        <div className="analysis-card__titles">
          <h2 className="analysis-card__title">{section.title}</h2>
          {section.lead ? <p className="analysis-card__lead">{section.lead}</p> : null}
        </div>
        <div className="analysis-card__badges">
          {statusBadge}
          {levelBadge}
        </div>
      </header>

      <div className="analysis-card__body">
        {section.kind === 'plan' && (
          <div className="analysis-plan">
            {section.routeRule || section.routeReason ? (
              <p className="analysis-plan__route">
                选择依据：{section.routeRule ? `命中「${section.routeRule}」` : ''}
                {section.routeReason ? `——${section.routeReason}` : ''}
              </p>
            ) : null}
            {section.overallSummary ? (
              <p className="analysis-plan__summary">{section.overallSummary}</p>
            ) : null}
            <MetricRow metrics={section.metrics ?? []} />
            <ReadinessMatrix section={section} />
          </div>
        )}

        {section.kind === 'module' && section.status === 'completed' && (
          <>
            <MetricRow metrics={section.metrics ?? []} />
            <div className="analysis-card__cols">
              <div className="analysis-card__main">
                {section.summary ? (
                  <p className="analysis-card__conclusion">{section.summary}</p>
                ) : null}
                <EvidenceBlock evidence={section.evidence ?? []} />
              </div>
              <div className="analysis-card__side">
                {section.legendGroups && section.legendGroups.length ? (
                  <AnalysisLegend groups={section.legendGroups} />
                ) : null}
                {section.funnel && section.funnel.length ? <Funnel steps={section.funnel} /> : null}
              </div>
            </div>
          </>
        )}

        {section.kind === 'module' && section.status === 'blocked' && (
          <div className="analysis-card__blocked">
            <p className="analysis-card__blocked-reason">{section.summary}</p>
            <div className="analysis-card__blocked-meta">
              {section.stopCode ? <span className="badge badge--neutral">{section.stopCode}</span> : null}
              {section.degradeHint ? (
                <p className="analysis-card__degrade">
                  <span className="analysis-card__degrade-label">还能做什么：</span>
                  {section.degradeHint}
                </p>
              ) : null}
            </div>
          </div>
        )}

        {section.kind === 'boundary' && (
          <div className="analysis-boundary">
            {section.summary ? <p className="analysis-boundary__summary">{section.summary}</p> : null}
            <MetricRow metrics={section.metrics ?? []} />
            <ul className="analysis-boundary__list">
              {(section.boundaryItems ?? []).map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <Limitations items={section.limitations ?? []} />

      {(section.runId || section.outputLayer) && (
        <footer className="analysis-card__footer">
          {section.runId ? (
            <span className="analysis-card__run">
              运行编号 <code>{section.runId}</code>
            </span>
          ) : null}
          {section.outputLayer ? (
            <button type="button" className="button button--secondary button--sm" onClick={viewOnMap}>
              <Locate aria-hidden="true" />
              在地图上查看
            </button>
          ) : null}
        </footer>
      )}
    </section>
  )
}
