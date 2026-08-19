import { X } from 'lucide-react'
import type { ResultWidget as ResultWidgetModel } from './resultTypes'
import { ResultWidgetValue, widgetTypeLabel } from './ResultWidget'

/** 成果卡「预览放大」：大尺寸重渲染单个成果，可滚动查看 */
export function WidgetPreviewModal({
  widget,
  onClose,
}: {
  widget: ResultWidgetModel
  onClose: () => void
}) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--lg"
        role="dialog"
        aria-modal="true"
        aria-label={widget.title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">{widget.title}</h3>
            <p className="modal__desc">
              <span className="badge badge--neutral">{widgetTypeLabel[widget.widgetType]}</span>
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭预览">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          {/* 大图渲染：图表 480 高，图片全宽 */}
          <ResultWidgetValue widget={widget} chartHeight={480} />
        </div>
      </div>
    </div>
  )
}
