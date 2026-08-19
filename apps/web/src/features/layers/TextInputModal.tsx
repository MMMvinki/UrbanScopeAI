import { useState } from 'react'
import { X } from 'lucide-react'

interface TextInputModalProps {
  title: string
  desc: string
  initialValue?: string
  placeholder?: string
  confirmLabel?: string
  onConfirm: (value: string) => void
  onClose: () => void
}

/** 通用单输入框弹窗（重命名图层等） */
export function TextInputModal({
  title,
  desc,
  initialValue,
  placeholder,
  confirmLabel = '确定',
  onConfirm,
  onClose,
}: TextInputModalProps) {
  const [value, setValue] = useState(initialValue ?? '')

  const handleConfirm = () => {
    const trimmed = value.trim()
    if (!trimmed) return
    onConfirm(trimmed)
    onClose()
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--sm"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">{title}</h3>
            <p className="modal__desc">{desc}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          <div className="form-field">
            <input
              type="text"
              className="form-field__input"
              value={value}
              placeholder={placeholder}
              autoFocus
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleConfirm()
              }}
            />
          </div>
        </div>
        <div className="modal__footer">
          <button type="button" className="button button--ghost" onClick={onClose}>
            取消
          </button>
          <button type="button" className="button button--primary" onClick={handleConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
