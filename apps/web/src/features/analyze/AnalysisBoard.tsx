import { useEffect } from 'react'
import { ArrowLeft, LayoutDashboard } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { buildSections } from './analysisMapper'
import { SectionCard } from './SectionCard'

interface AnalysisBoardProps {
  onExit: () => void
}

/**
 * 综合分析板块整页板面：替换 AppShell，16:9 全宽从上到下顺序阅读。
 * 顶栏（标题 + 返回地图）+ 顶部锚点导航 + 板块卡（计划 / 就绪矩阵 / 三个核心板块 / 边界）。
 * 数据 100% 来自 store.lastComprehensiveAnalysis（即后端 structured_analysis）。
 */
export function AnalysisBoard({ onExit }: AnalysisBoardProps) {
  const analysis = useAppStore((s) => s.lastComprehensiveAnalysis)

  const sections = analysis ? buildSections(analysis) : []

  const { analysisFocus, setAnalysisFocus } = useAppStore((s) => ({
    analysisFocus: s.analysisFocus,
    setAnalysisFocus: s.setAnalysisFocus,
  }))

  const scrollTo = (id: string) => {
    document.getElementById(`analysis-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // 对话「分析页查看」跳转：进入分析页后对准目标板块卡，随后清除避免重复滚动
  useEffect(() => {
    if (analysisFocus) {
      const target = analysisFocus.id
      setAnalysisFocus(null)
      const t = window.setTimeout(() => scrollTo(target), 60)
      return () => window.clearTimeout(t)
    }
  }, [analysisFocus, setAnalysisFocus])

  return (
    <div className="analysis-board">
      <header className="analysis-board__header">
        <div className="analysis-board__title">
          <LayoutDashboard aria-hidden="true" />
          <div>
            <h1>综合分析</h1>
            <p className="analysis-board__subtitle">
              按规划板块从上到下阅读 · 数字与地图均来自同一次真实运行（structured_analysis）
            </p>
          </div>
        </div>
        <button type="button" className="button button--ghost" onClick={onExit}>
          <ArrowLeft aria-hidden="true" /> 返回地图
        </button>
      </header>

      {!analysis ? (
        <div className="analysis-board__empty">
          <p className="analysis-board__empty-title">还没有综合分析结果</p>
          <p className="analysis-board__empty-desc">
            先在对话中提问「根据当前已有数据，对项目各方面进行一次尽可能完整的现状分析」，
            完成一次综合分析后，这里会按板块展示真实结果。
          </p>
          <button type="button" className="button button--primary" onClick={onExit}>
            <ArrowLeft aria-hidden="true" /> 返回地图发起分析
          </button>
        </div>
      ) : (
        <>
          <nav className="analysis-nav" aria-label="板块导航">
            {sections.map((section) => (
              <button
                type="button"
                className="analysis-nav__item"
                key={section.id}
                onClick={() => scrollTo(section.id)}
              >
                <span className="analysis-nav__index">{section.index + 1}</span>
                {section.title}
              </button>
            ))}
          </nav>

          <div className="analysis-board__body">
            {sections.map((section) => (
              <SectionCard section={section} key={section.id} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
