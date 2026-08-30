import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  ClipboardCheck,
  Download,
  Play,
  RefreshCw,
  X,
} from 'lucide-react'
import { API_BASE } from '../../lib/api'
import {
  fetchEvalReport,
  fetchEvalReports,
  fetchEvalRun,
  fetchEvalSet,
  fetchEvalSets,
  triggerEvalRun,
} from '../../lib/api'
import type {
  EvalCaseDetail,
  EvalReportDetail,
  EvalReportSummary,
  EvalRunStatus,
  EvalSetDetail,
  EvalSetSummary,
} from './evalTypes'

interface EvalAdminPageProps {
  onExit: () => void
}

/** ISO 时间 → 本地可读（"2026-08-26T02:24:00" → "2026-08-26 02:24"） */
function formatTime(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

function outcomeBadge(outcome: string): string {
  switch (outcome) {
    case 'success':
      return 'badge--success'
    case 'refuse':
      return 'badge--warning'
    case 'degraded':
      return 'badge--warning'
    default:
      return 'badge--neutral'
  }
}

/** 评估集列表块：头部折叠 + 展开后展示 description/tiers + case 列表 */
function SetBlock({
  set,
  expanded,
  detail,
  onToggle,
  onOpenCase,
}: {
  set: EvalSetSummary
  expanded: boolean
  detail: EvalSetDetail | null
  onToggle: () => void
  onOpenCase: (caseItem: EvalCaseDetail) => void
}) {
  return (
    <li className="eval-admin__set">
      <button
        type="button"
        className="eval-admin__set-head"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <ChevronRight
          className={expanded ? 'eval-admin__chevron is-open' : 'eval-admin__chevron'}
          aria-hidden="true"
        />
        <span className="eval-admin__set-name">{set.name}</span>
        <span className="eval-admin__set-count">{set.case_count} 条</span>
      </button>
      {expanded && (
        <div className="eval-admin__set-body">
          {set.error ? <div className="eval-admin__notice">解析失败：{set.error}</div> : null}
          {set.description ? <p className="eval-admin__set-desc">{set.description}</p> : null}
          <div className="eval-admin__tiers">
            {Object.entries(set.tiers).map(([tier, count]) =>
              count > 0 ? (
                <span className="badge badge--neutral" key={tier}>
                  {tier} × {count}
                </span>
              ) : null,
            )}
          </div>
          {detail ? (
            <ul className="eval-admin__case-list">
              {detail.cases.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className="eval-admin__case"
                    onClick={() => onOpenCase(c)}
                    title="查看用例详情"
                  >
                    <span className="eval-admin__case-title">{c.title}</span>
                    <span className="eval-admin__case-badges">
                      <span className="badge badge--neutral">{c.category}</span>
                      <span className="badge badge--neutral">{c.difficulty}</span>
                      <span className={`badge ${outcomeBadge(c.expected.expected_outcome)}`}>
                        {c.expected.expected_outcome}
                      </span>
                      {c.tool ? <span className="badge badge--success">{c.tool}</span> : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="eval-admin__notice">加载中…</div>
          )}
        </div>
      )}
    </li>
  )
}

/** 报告历史行：id + 通过率徽章 + set/tier + fail_reasons 摘要 */
function ReportRow({
  report,
  selected,
  onOpen,
}: {
  report: EvalReportSummary
  selected: boolean
  onOpen: () => void
}) {
  const rate = report.pass_rate
  const rateClass = rate === 1 ? 'is-pass' : rate >= 0.5 ? 'is-partial' : 'is-fail'
  return (
    <li>
      <button
        type="button"
        className={`eval-admin__report ${selected ? 'is-selected' : ''}`}
        onClick={onOpen}
      >
        <span className="eval-admin__report-top">
          <span className="eval-admin__report-id">{report.id}</span>
          <span className={`eval-admin__rate ${rateClass}`}>{Math.round(rate * 100)}%</span>
        </span>
        <span className="eval-admin__report-meta">
          <span className="badge badge--neutral">{report.set}</span>
          <span className="badge badge--neutral">{report.tier}</span>
          <span>
            {report.passed}/{report.total} 通过
          </span>
          <span className="eval-admin__report-time">{formatTime(report.updated_at)}</span>
        </span>
        {report.failed > 0 ? (
          <span className="eval-admin__report-fail">
            {Object.entries(report.fail_reasons).length > 0
              ? Object.entries(report.fail_reasons)
                  .map(([reason, n]) => `${reason} ×${n}`)
                  .join(' · ')
              : `${report.failed} 未通过`}
          </span>
        ) : (
          <span className="eval-admin__report-fail is-clean">全部通过</span>
        )}
      </button>
    </li>
  )
}

/** JSON 小节：标题 + 等宽 pre */
function JsonBlock({ title, data }: { title: string; data: unknown }) {
  return (
    <section className="modal__section">
      <h4 className="modal__section-title">{title}</h4>
      <pre className="eval-admin__json">{JSON.stringify(data, null, 2)}</pre>
    </section>
  )
}

/** 单个 case 详情弹窗：params / expected / ground_truth / fault / safety 全 JSON 展示 */
function CaseDetailModal({ caseItem, onClose }: { caseItem: EvalCaseDetail; onClose: () => void }) {
  const c = caseItem
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal eval-admin__case-modal"
        role="dialog"
        aria-modal="true"
        aria-label={c.title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <div className="modal__titles">
            <h3 className="modal__title">{c.title}</h3>
            <div className="eval-admin__case-modal-badges">
              <span className="badge badge--neutral">{c.id}</span>
              <span className="badge badge--neutral">{c.category}</span>
              <span className="badge badge--neutral">{c.difficulty}</span>
              <span className="badge badge--neutral">{c.tier}</span>
              {c.tool ? <span className="badge badge--success">{c.tool}</span> : null}
              {c.depends_external !== 'none' ? (
                <span className="badge badge--warning">外部依赖</span>
              ) : null}
            </div>
          </div>
          <button type="button" className="icon-button icon-button--sm" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          {c.expected.intent ? <JsonBlock title="意图" data={c.expected.intent} /> : null}
          <JsonBlock title="参数" data={c.params} />
          {c.setup ? (
            <JsonBlock
              title="准备（写入工作区）"
              data={c.setup.map((s) => ({ path: s.path, ...(s.content ? { content: s.content } : {}) }))}
            />
          ) : null}
          <JsonBlock title="期望结果" data={c.expected} />
          <JsonBlock title="真值（Ground Truth）" data={c.ground_truth} />
          {c.fault ? <JsonBlock title="故障注入" data={c.fault} /> : null}
          <JsonBlock title="安全护栏" data={c.safety} />
        </div>
      </div>
    </div>
  )
}

/** 报告详情弹窗：总览卡片 + by_category/by_tool + fail_reasons + 逐条结果表 + 下载 .md */
function ReportDetailModal({
  report,
  onClose,
}: {
  report: EvalReportDetail
  onClose: () => void
}) {
  const { meta, summary, results } = report
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal eval-admin__report-modal"
        role="dialog"
        aria-modal="true"
        aria-label={report.id}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <div className="modal__titles">
            <h3 className="modal__title">评测报告</h3>
            <p className="modal__desc">
              {report.id} · {meta.set} · {meta.tier} · {formatTime(report.updated_at)}
            </p>
          </div>
          <div className="eval-admin__modal-actions">
            <a
              className="button button--ghost button--sm"
              href={`${API_BASE}/eval/reports/${encodeURIComponent(report.id)}/download`}
              target="_blank"
              rel="noreferrer"
            >
              <Download aria-hidden="true" /> 下载 .md
            </a>
            <button type="button" className="icon-button icon-button--sm" onClick={onClose} aria-label="关闭">
              <X aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="modal__body">
          <section className="modal__section">
            <h4 className="modal__section-title">总览</h4>
            <div className="eval-admin__summary-cards">
              <div className="eval-admin__summary-card">
                <strong>{Math.round(summary.pass_rate * 100)}%</strong>
                <span>通过率</span>
              </div>
              <div className="eval-admin__summary-card">
                <strong>{summary.passed}</strong>
                <span>通过</span>
              </div>
              <div className="eval-admin__summary-card is-fail">
                <strong>{summary.failed}</strong>
                <span>失败</span>
              </div>
              <div className="eval-admin__summary-card">
                <strong>{summary.total}</strong>
                <span>总计</span>
              </div>
            </div>
          </section>
          <section className="modal__section">
            <h4 className="modal__section-title">按分类 / 按工具</h4>
            <div className="eval-admin__breakdown">
              <div className="eval-admin__breakdown-col">
                <h5>按分类</h5>
                {Object.keys(summary.by_category).length === 0 ? (
                  <p className="eval-admin__muted">—</p>
                ) : (
                  Object.entries(summary.by_category).map(([key, value]) => (
                    <p key={key}>
                      <span>{key}</span>
                      <em>{value}</em>
                    </p>
                  ))
                )}
              </div>
              <div className="eval-admin__breakdown-col">
                <h5>按工具</h5>
                {Object.keys(summary.by_tool).length === 0 ? (
                  <p className="eval-admin__muted">—</p>
                ) : (
                  Object.entries(summary.by_tool).map(([key, value]) => (
                    <p key={key}>
                      <span>{key}</span>
                      <em>{value}</em>
                    </p>
                  ))
                )}
              </div>
            </div>
          </section>
          {summary.failed > 0 ? (
            <section className="modal__section">
              <h4 className="modal__section-title">失败原因</h4>
              <div className="eval-admin__fail-reasons">
                {Object.entries(summary.fail_reasons).map(([reason, n]) => (
                  <span className="badge badge--error" key={reason}>
                    {reason} ×{n}
                  </span>
                ))}
              </div>
            </section>
          ) : null}
          <section className="modal__section">
            <h4 className="modal__section-title">逐条结果</h4>
            <table className="eval-admin__results">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>用例</th>
                  <th>分类</th>
                  <th>工具</th>
                  <th>期望</th>
                  <th>检查</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.id} className={r.passed ? 'is-pass' : 'is-fail'}>
                    <td>
                      <span
                        className={r.passed ? 'eval-admin__dot is-pass' : 'eval-admin__dot is-fail'}
                        aria-label={r.passed ? '通过' : '失败'}
                      />
                    </td>
                    <td className="eval-admin__results-title">
                      {r.title}
                      {r.error ? <span className="eval-admin__results-err">{r.error}</span> : null}
                    </td>
                    <td>{r.category}</td>
                    <td>{r.tool ?? '—'}</td>
                    <td>{r.outcome}</td>
                    <td>
                      <ul className="eval-admin__checks">
                        {r.checks.map((c) => (
                          <li key={c.name} className={c.ok ? 'is-ok' : 'is-bad'}>
                            <span className="eval-admin__check-mark">{c.ok ? '✓' : '✗'}</span>
                            <span className="eval-admin__check-name">{c.name}</span>
                            {!c.ok && c.detail ? (
                              <span className="eval-admin__check-detail">{c.detail}</span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      </div>
    </div>
  )
}

/**
 * 评测管理后台（独立整页，替换 AppShell）。
 * 范式照 SkillPanel：本地 state + useEffect 取数 + 刷新 + 弹窗 + busy 态。
 * 运行评测走子进程（POST /eval/run），页面每 1.5s 轮询 GET /eval/runs/{id}。
 */
export function EvalAdminPage({ onExit }: EvalAdminPageProps) {
  const [sets, setSets] = useState<EvalSetSummary[]>([])
  const [reports, setReports] = useState<EvalReportSummary[]>([])

  const [expandedSet, setExpandedSet] = useState<string | null>(null)
  const [setDetail, setSetDetail] = useState<EvalSetDetail | null>(null)

  const [runSet, setRunSet] = useState('all')
  const [runTier, setRunTier] = useState('tool')
  const [excludeDep, setExcludeDep] = useState(false)
  const [starting, setStarting] = useState(false)
  const [runStatus, setRunStatus] = useState<EvalRunStatus | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  const [detailCase, setDetailCase] = useState<EvalCaseDetail | null>(null)
  const [reportId, setReportId] = useState<string | null>(null)
  const [reportDetail, setReportDetail] = useState<EvalReportDetail | null>(null)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const openReport = useCallback(async (id: string) => {
    setReportId(id)
    try {
      setReportDetail(await fetchEvalReport(id))
    } catch {
      setReportDetail(null)
    }
  }, [])

  const loadAll = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([fetchEvalSets(), fetchEvalReports()])
      setSets(s)
      setReports(r)
    } catch {
      // 后端未启动：页面保持空态提示
    }
  }, [])

  useEffect(() => {
    void loadAll()
    return stopPolling
  }, [loadAll, stopPolling])

  const toggleSet = async (name: string) => {
    if (expandedSet === name) {
      setExpandedSet(null)
      setSetDetail(null)
      return
    }
    setExpandedSet(name)
    setSetDetail(null)
    try {
      setSetDetail(await fetchEvalSet(name))
    } catch {
      setSetDetail(null)
    }
  }

  const startRun = async () => {
    setRunError(null)
    setStarting(true)
    try {
      const { run_id } = await triggerEvalRun({
        set: runSet,
        tier: runTier,
        exclude_dep: excludeDep,
      })
      setRunStatus({ state: 'running' })
      setReportId(null)
      setReportDetail(null)
      pollRef.current = setInterval(async () => {
        try {
          const status = await fetchEvalRun(run_id)
          setRunStatus(status)
          if (status.state !== 'running') {
            stopPolling()
            if (status.state === 'done') {
              await loadAll()
              if (status.report_id) void openReport(status.report_id)
            } else {
              setRunError(status.error || '评测运行失败')
            }
          }
        } catch {
          // 单次轮询失败忽略，下次重试
        }
      }, 1500)
    } catch {
      setRunError('评测启动失败：已有评测运行中，或后端不可用')
    } finally {
      setStarting(false)
    }
  }

  const running = runStatus?.state === 'running'
  const progressPct =
    runStatus && runStatus.total
      ? Math.round(((runStatus.current || 0) / runStatus.total) * 100)
      : 0

  return (
    <div className="eval-admin">
      <header className="eval-admin__header">
        <div className="eval-admin__title">
          <ClipboardCheck aria-hidden="true" />
          <div>
            <h1>评测管理</h1>
            <p className="eval-admin__subtitle">
              tool 真值回归 · plan 规划层 · e2e 全链路 · 设计文档 docs/eval-system-design.md · 运行隔离于子进程，不污染图层面板
            </p>
          </div>
        </div>
        <div className="eval-admin__header-actions">
          <button
            type="button"
            className="button button--ghost"
            onClick={() => void loadAll()}
            title="刷新评估集与报告"
          >
            <RefreshCw aria-hidden="true" /> 刷新
          </button>
          <button type="button" className="button" onClick={onExit}>
            <ArrowLeft aria-hidden="true" /> 返回地图
          </button>
        </div>
      </header>

      <div className="eval-admin__runbar">
        <div className="eval-admin__runbar-field">
          <label htmlFor="eval-run-set">评估集</label>
          <select
            id="eval-run-set"
            value={runSet}
            onChange={(event) => setRunSet(event.target.value)}
          >
            <option value="all">全部</option>
            {sets.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="eval-admin__runbar-field">
          <label htmlFor="eval-run-tier">级别</label>
          <select id="eval-run-tier" value={runTier} onChange={(event) => setRunTier(event.target.value)}>
            <option value="tool">tool（真值回归，免费）</option>
            <option value="plan">plan（仅规划，~40 次 LLM）</option>
            <option value="e2e">e2e（全链路，~10 条·每条多轮 LLM）</option>
            <option value="plan,e2e">plan+e2e</option>
          </select>
        </div>
        <label className="eval-admin__runbar-check">
          <input
            type="checkbox"
            checked={excludeDep}
            onChange={(event) => setExcludeDep(event.target.checked)}
          />
          跳过外部依赖（amap / mapbox）
        </label>
        <button
          type="button"
          className="button button--primary"
          onClick={() => void startRun()}
          disabled={starting || running}
        >
          <Play aria-hidden="true" /> {starting ? '启动中…' : running ? '运行中…' : '运行评测'}
        </button>
        {running ? (
          <div className="eval-admin__progress">
            <span className="eval-admin__progress-text">
              {runStatus?.total
                ? `${runStatus.passed ?? 0}/${runStatus.total} 通过 · 正在跑 ${runStatus.current_case ?? ''}`
                : '准备中…'}
            </span>
            <div className="eval-admin__progress-bar">
              <div className="eval-admin__progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
          </div>
        ) : null}
        {runError ? <div className="eval-admin__error">{runError}</div> : null}
      </div>

      <div className="eval-admin__body">
        <section className="eval-admin__col eval-admin__col--sets">
          <h2 className="eval-admin__section-title">评估集</h2>
          {sets.length === 0 ? (
            <div className="eval-admin__empty">
              未发现评估集。请确认后端已启动，且 apps/api/eval_sets/ 下有评估集 YAML。
            </div>
          ) : (
            <ul className="eval-admin__set-list">
              {sets.map((s) => (
                <SetBlock
                  key={s.name}
                  set={s}
                  expanded={expandedSet === s.name}
                  detail={expandedSet === s.name ? setDetail : null}
                  onToggle={() => void toggleSet(s.name)}
                  onOpenCase={setDetailCase}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="eval-admin__col eval-admin__col--reports">
          <h2 className="eval-admin__section-title">报告历史</h2>
          {reports.length === 0 ? (
            <div className="eval-admin__empty">尚无报告，点上方「运行评测」生成第一条。</div>
          ) : (
            <ul className="eval-admin__report-list">
              {reports.map((r) => (
                <ReportRow
                  key={r.id}
                  report={r}
                  selected={reportId === r.id}
                  onOpen={() => void openReport(r.id)}
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      {detailCase ? (
        <CaseDetailModal caseItem={detailCase} onClose={() => setDetailCase(null)} />
      ) : null}
      {reportDetail ? (
        <ReportDetailModal report={reportDetail} onClose={() => setReportDetail(null)} />
      ) : null}
    </div>
  )
}
