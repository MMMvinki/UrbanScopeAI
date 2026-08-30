import type { LayerMetadata, LayerStyle } from '../layers/layerTypes'
import { featureCollectionBounds, getLayerGeojson } from './mapLayers'

/**
 * SVG 示意地图：WebGL 不可用时把同一套确定性图层几何画成平面示意图，
 * 保证地图工作区在无 GPU 环境（虚拟机 / 远程桌面 / 禁用硬件加速）下仍然有内容。
 * 样式与 deck.gl 路径一致：优先读 layerStyles（用户改的填充色/透明度/描边/半径），
 * 缺省回退 layer.color —— 否则兜底图会"改样式没反应"。
 */
interface MapSchematicProps {
  layers: LayerMetadata[]
  styles?: Record<string, LayerStyle>
}

const VIEW_W = 640
const VIEW_H = 420

/**
 * 把任意嵌套 coordinates 展平成坐标对（示意地图专用）。
 * 后端真实几何可能是 LineString / MultiLineString / Polygon / MultiPolygon，
 * 嵌套层数各不相同，绝不能按固定层数展开；坏数据静默跳过，不崩图。
 */
function flattenCoords(coords: unknown, out: Array<[number, number]> = []): Array<[number, number]> {
  if (!Array.isArray(coords)) return out
  if (coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
    out.push([coords[0], coords[1]])
    return out
  }
  for (const child of coords) flattenCoords(child, out)
  return out
}

export function MapSchematic({ layers, styles }: MapSchematicProps) {
  const visible = layers.filter((layer) => layer.visible)
  const items = visible.flatMap((layer) =>
    getLayerGeojson(layer).features.map((feature) => ({ layer, feature })),
  )

  if (items.length === 0) {
    return (
      <p className="map-fallback__hint" style={{ margin: 0 }}>
        当前没有可见图层。
      </p>
    )
  }

  // 所有可见要素的包围盒 → 线性投影到 viewBox（保持比例、居中）
  const collection: Parameters<typeof featureCollectionBounds>[0] = {
    type: 'FeatureCollection',
    features: items.map((item) => item.feature),
  }
  const [[minLng, minLat], [maxLng, maxLat]] = featureCollectionBounds(collection)
  const bw = maxLng - minLng || 1e-9
  const bh = maxLat - minLat || 1e-9
  const scale = Math.min(VIEW_W / bw, VIEW_H / bh)
  const ox = (VIEW_W - bw * scale) / 2
  const oy = (VIEW_H - bh * scale) / 2
  const px = (lng: number) => ox + (lng - minLng) * scale
  const py = (lat: number) => VIEW_H - (oy + (lat - minLat) * scale)

  return (
    <svg
      className="map-schematic"
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      role="img"
      aria-label="示意地图"
    >
      <rect x={0} y={0} width={VIEW_W} height={VIEW_H} rx={8} fill="#eef2f7" />
      {items.map(({ layer, feature }, index) => {
        const style = styles?.[layer.id]
        const fill = style?.fillColor ?? layer.color
        const stroke = style?.strokeColor ?? layer.color
        if (feature.geometry.type === 'Point') {
          const [lng, lat] = feature.geometry.coordinates
          return (
            <circle
              key={index}
              cx={px(lng)}
              cy={py(lat)}
              r={style?.radius ?? 4}
              fill={fill}
              fillOpacity={style?.opacity ?? 1}
              stroke={style?.strokeColor && style.strokeWidth ? stroke : '#ffffff'}
              strokeWidth={style?.strokeWidth ?? 1.2}
            />
          )
        }
        const fillOpacity = style?.opacity ?? 0.8
        // 按几何类型展开子路径：线不闭合、面闭合；嵌套层数不同，逐环 flatten（坏数据跳过不崩）
        const rings: Array<Array<[number, number]>> =
          feature.geometry.type === 'LineString'
            ? [flattenCoords(feature.geometry.coordinates)]
            : feature.geometry.type === 'MultiLineString'
              ? feature.geometry.coordinates.map((line) => flattenCoords(line))
              : feature.geometry.type === 'MultiPolygon'
                ? feature.geometry.coordinates.flatMap((poly) => poly.map((ring) => flattenCoords(ring)))
                : feature.geometry.coordinates.map((ring) => flattenCoords(ring))
        const isArea = feature.geometry.type === 'Polygon' || feature.geometry.type === 'MultiPolygon'
        const path = rings
          .map(
            (ring) =>
              ring.map(([x, y], i) => `${i ? 'L' : 'M'}${px(x).toFixed(1)},${py(y).toFixed(1)}`).join(' ') +
              (isArea ? 'Z' : ''),
          )
          .join(' ')
        return (
          <path
            key={index}
            d={path}
            fill={fill}
            fillOpacity={fillOpacity}
            stroke={stroke}
            strokeWidth={style?.strokeWidth ?? (layer.id === 'study_area' ? 2 : 0.6)}
          />
        )
      })}
    </svg>
  )
}
