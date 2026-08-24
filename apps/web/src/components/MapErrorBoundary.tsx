import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  message?: string
}

/**
 * 地图错误边界：某个图层的坏几何（投影坐标/NaN/越界）导致渲染或定位异常时，
 * 兜底为可恢复提示，而不是 React 无错误边界时整站白屏。
 */
export class MapErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(error: unknown): State {
    return { hasError: true, message: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[MapErrorBoundary] 地图渲染异常：', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="map-fallback" role="alert">
          <div className="map-fallback__note">
            <p className="map-fallback__title">地图渲染出错了</p>
            <p className="map-fallback__hint">
              {this.state.message}。通常是某个图层几何数据有问题（坐标越界 / 投影坐标未转成经纬度）。
              可以隐藏有问题的图层后再试，或点击重新加载。
            </p>
            <button type="button" onClick={() => window.location.reload()}>
              重新加载
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
