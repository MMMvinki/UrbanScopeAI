import { ClipboardList } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'

/**
 * 对话「成果产物」chip（改动 6）：每条完成回复下一行摘要，点击跳转成果页并高亮该 run 分组。
 * 一行 = `成果产物（N）· 图层x · 图表y`：
 *   N    = 该分组 widget 总数
 *   x    = 各 map widget data.layerIds 长度之和
 *   y    = chart + image widget 数量
 * 消息持久化但成果是内存态：刷新后 group 缺失 → 返回 null 不渲染。
 */
export function ResultChip({ groupId }: { groupId: string }) {
  const group = useAppStore((s) => s.resultGroups.find((item) => item.id === groupId))
  const focusResultGroup = useAppStore((s) => s.focusResultGroup)

  if (!group || group.widgets.length === 0) return null

  const mapLayers = group.widgets.reduce((count, widget) => {
    if (widget.widgetType === 'map') return count + (widget.data?.layerIds?.length ?? 0)
    return count
  }, 0)
  const charts = group.widgets.reduce(
    (count, widget) =>
      count + (widget.widgetType === 'chart' || widget.widgetType === 'image' ? 1 : 0),
    0,
  )

  return (
    <button
      type="button"
      className="chat-chip"
      onClick={() => focusResultGroup(group.id)}
      title="在成果页查看本次分析的全部产物"
    >
      <ClipboardList aria-hidden="true" />
      成果产物（{group.widgets.length}）· 图层{mapLayers} · 图表{charts}
    </button>
  )
}
