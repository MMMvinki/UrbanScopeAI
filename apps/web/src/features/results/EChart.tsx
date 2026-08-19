import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import * as echarts from 'echarts'

/** 后端组装好的 ECharts option（含动画/悬浮/缩放/图例） */
export type EChartOption = Record<string, unknown>

/** EChart 实例句柄：成果导出经 getDataURL 拿 PNG；实例未就绪返回空串 */
export interface EChartHandle {
  getDataURL: () => string
}

/**
 * 交互式图表：ECharts 渲染，支持悬浮查看数值、拖拽缩放（dataZoom）、入场动画。
 * 一卡一图，高度可由后端 spec 指定。forwardRef 暴露 getDataURL 供导出 PNG。
 */
export const EChart = forwardRef<EChartHandle, { option: EChartOption; height?: number }>(
  function EChart({ option, height = 320 }, ref) {
    const containerRef = useRef<HTMLDivElement>(null)
    const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null)

    // 初始化一次：创建实例 + 跟随容器尺寸 resize
    useEffect(() => {
      const element = containerRef.current
      if (!element) return
      const chart = echarts.init(element)
      chartRef.current = chart
      const observer = new ResizeObserver(() => chart.resize())
      observer.observe(element)
      return () => {
        observer.disconnect()
        chart.dispose()
        chartRef.current = null
      }
    }, [])

    // option 更新（含首次 setOption）→ 以 notMerge 全量替换
    useEffect(() => {
      chartRef.current?.setOption(option as echarts.EChartsOption, true)
    }, [option])

    // 导出：组件卸载/实例未就绪时返回空串，由调用方禁用导出按钮
    useImperativeHandle(
      ref,
      () => ({
        getDataURL: () =>
          chartRef.current?.getDataURL({ type: 'png', pixelRatio: 2 }) ?? '',
      }),
      [],
    )

    return <div ref={containerRef} style={{ width: '100%', height }} />
  },
)
