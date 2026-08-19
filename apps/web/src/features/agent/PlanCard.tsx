import { Check, Circle, ListChecks, Loader2, TriangleAlert } from 'lucide-react'
import type { PlanStep, StepStatus } from './agentTypes'

/** 计划进度卡（对齐典型计划进度卡）：标题 + 进度条 + 步骤清单（完成划线） */
export function PlanCard({ steps, title = '分析计划' }: { steps: PlanStep[]; title?: string }) {
  const total = steps.length
  const done = steps.filter((s) => s.status === 'done').length
  const allDone = total > 0 && done >= total

  if (total === 0) return null

  return (
    <div className="chat-plan">
      <div className="chat-plan__header">
        <ListChecks aria-hidden="true" />
        <span className="chat-plan__title">{title}</span>
        <span className="chat-plan__count">
          {done}/{total}
        </span>
      </div>
      <div className="chat-plan__rail" aria-hidden="true">
        {steps.map((step, index) => (
          <span
            key={index}
            className={`chat-plan__rail-seg ${railClass(step.status, allDone)}`}
            title={step.label}
          />
        ))}
      </div>
      <ul className="chat-plan__steps">
        {steps.map((step, index) => (
          <li key={index} className={`chat-plan__step chat-plan__step--${step.status}`}>
            <StepIcon status={step.status} />
            <span>{step.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function railClass(status: StepStatus, allDone: boolean): string {
  if (allDone) return 'chat-plan__rail-seg--done'
  switch (status) {
    case 'done':
      return 'chat-plan__rail-seg--done'
    case 'running':
      return 'chat-plan__rail-seg--running'
    case 'warning':
      return 'chat-plan__rail-seg--warning'
    default:
      return ''
  }
}

function StepIcon({ status }: { status: StepStatus }) {
  switch (status) {
    case 'done':
      return <Check aria-hidden="true" />
    case 'running':
      return <Loader2 className="spinner" aria-hidden="true" />
    case 'warning':
      return <TriangleAlert aria-hidden="true" />
    default:
      return <Circle aria-hidden="true" />
  }
}
