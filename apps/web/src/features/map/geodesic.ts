/**
 * geodesic.ts — 手绘图层自动字段的测地计算（点经纬度 / 线长度 / 面面积）与单位换算。
 *
 * 平台未引入 turf，这里用自算公式：
 * - 长度：haversine 大圆距离逐段累加，R = 6371008.8 m（平均球面半径，与 turf length 一致）。
 * - 面积：球面剩余公式（l'Huilier / turf area 同款），对闭合环只累加到 n-1 段，R = 6378137 m。
 *
 * 约定：所有出入参坐标均为 [lng, lat]（WGS84 / EPSG:4326），角用弧度。
 */

const EARTH_RADIUS_LENGTH = 6371008.8
const EARTH_RADIUS_AREA = 6378137

const degToRad = (deg: number) => (deg * Math.PI) / 180

/** 两坐标点的大圆距离（米） */
function haversine(a: number[], b: number[]): number {
  const [lng1, lat1] = [degToRad(a[0]), degToRad(a[1])]
  const [lng2, lat2] = [degToRad(b[0]), degToRad(b[1])]
  const dLat = lat2 - lat1
  const dLng = lng2 - lng1
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_RADIUS_LENGTH * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h))
}

/** 折线长度（米）：相邻点球面距离逐段累加 */
export function geodesicLength(coords: number[][]): number {
  let total = 0
  for (let i = 0; i < coords.length - 1; i++) {
    total += haversine(coords[i], coords[i + 1])
  }
  return total
}

/** 球面多边形面积（m²）：闭合环只累加 n-1 段；未闭合环自动补首尾 */
export function geodesicArea(ring: number[][]): number {
  if (ring.length < 3) return 0
  const n = ring[0][0] === ring[ring.length - 1][0] && ring[0][1] === ring[ring.length - 1][1]
    ? ring.length - 1
    : ring.length
  let total = 0
  for (let i = 0; i < n; i++) {
    const [lng1, lat1] = [degToRad(ring[i][0]), degToRad(ring[i][1])]
    const [lng2, lat2] = [degToRad(ring[(i + 1) % ring.length][0]), degToRad(ring[(i + 1) % ring.length][1])]
    total += (lng2 - lng1) * (2 + Math.sin(lat1) + Math.sin(lat2))
  }
  return Math.abs((total * EARTH_RADIUS_AREA * EARTH_RADIUS_AREA) / 2)
}

/** 长度单位换算（基准：米） */
export function convertLength(meters: number, unit: 'm' | 'km'): number {
  return unit === 'km' ? meters / 1000 : meters
}

/** 面积单位换算（基准：m²） */
export function convertArea(m2: number, unit: 'm2' | 'hm2' | 'km2'): number {
  if (unit === 'hm2') return m2 / 10000 // hm² = 公顷 = 10^4 m²
  if (unit === 'km2') return m2 / 1_000_000
  return m2
}

export type LengthUnit = 'm' | 'km'
export type AreaUnit = 'm2' | 'hm2' | 'km2'
export type AutoUnitConfig = { length?: LengthUnit; area?: AreaUnit }

/** 单位标签（作字段名后缀 / 展示用） */
export const UNIT_LABEL: Record<string, string> = {
  m: '米',
  km: '千米',
  m2: '平方米',
  hm2: '公顷',
  km2: '平方千米',
}

/** 面板单位下拉选项 */
export const LENGTH_UNITS: Array<{ value: LengthUnit; label: string }> = [
  { value: 'm', label: '米' },
  { value: 'km', label: '千米' },
]
export const AREA_UNITS: Array<{ value: AreaUnit; label: string }> = [
  { value: 'm2', label: '平方米' },
  { value: 'hm2', label: '公顷' },
  { value: 'km2', label: '平方千米' },
]

/** 单个要素测地长度（米）：LineString 逐段；MultiLineString 累计各段 */
export function featureLengthMeters(feature: GeoJSON.Feature): number {
  const g = feature.geometry
  if (g.type === 'LineString') return geodesicLength(g.coordinates as unknown as number[][])
  if (g.type === 'MultiLineString') {
    return (g.coordinates as unknown as number[][][]).reduce(
      (sum, line) => sum + geodesicLength(line), 0,
    )
  }
  return 0
}

/** 单个要素测地面积（m²）：Polygon 首环；MultiPolygon 累计各面 */
export function featureAreaM2(feature: GeoJSON.Feature): number {
  const g = feature.geometry
  if (g.type === 'Polygon') {
    return geodesicArea((g.coordinates[0] ?? []) as unknown as number[][])
  }
  if (g.type === 'MultiPolygon') {
    return (g.coordinates as unknown as number[][][][]).reduce(
      (sum, poly) => sum + geodesicArea((poly[0] ?? []) as unknown as number[][]), 0,
    )
  }
  return 0
}

/** 计算某要素的自动字段（含单位后缀字段名）与值。点→经度/纬度，线→长度，面→面积。 */
export function computeAutoFields(
  feature: GeoJSON.Feature,
  units: AutoUnitConfig = {},
): Record<string, number> {
  const { length = 'm', area = 'm2' } = units
  const g = feature.geometry
  if (g.type === 'Point' || g.type === 'MultiPoint') {
    const [lng, lat] = g.type === 'Point'
      ? (g.coordinates as unknown as number[])
      : ((g.coordinates as unknown as number[][])[0] ?? [])
    return { '经度': lng, '纬度': lat }
  }
  if (g.type === 'LineString' || g.type === 'MultiLineString') {
    return { [`长度(${UNIT_LABEL[length]})`]: convertLength(featureLengthMeters(feature), length) }
  }
  if (g.type === 'Polygon' || g.type === 'MultiPolygon') {
    return { [`面积(${UNIT_LABEL[area]})`]: convertArea(featureAreaM2(feature), area) }
  }
  return {}
}

/** 根据要素集合判断需要哪些自动列（点/线/面）—— 供面板增列与字段 schema 用 */
export function autoFieldKeysFor(
  features: GeoJSON.Feature[],
  units: AutoUnitConfig = {},
): string[] {
  const hasPoint = features.some((f) => f.geometry.type === 'Point' || f.geometry.type === 'MultiPoint')
  const hasLine = features.some((f) => f.geometry.type === 'LineString' || f.geometry.type === 'MultiLineString')
  const hasPoly = features.some((f) => f.geometry.type === 'Polygon' || f.geometry.type === 'MultiPolygon')
  const keys: string[] = []
  if (hasPoint) keys.push('经度', '纬度')
  if (hasLine) keys.push(`长度(${UNIT_LABEL[units.length ?? 'm']})`)
  if (hasPoly) keys.push(`面积(${UNIT_LABEL[units.area ?? 'm2']})`)
  return keys
}

/** 任一单位变体的自动字段键（点/线/面 + 长度/面积带单位后缀）；用于清除旧单位残留列 */
const AUTO_KEY_RE = /^(经度|纬度|长度\(.+\)|面积\(.+\))$/

export function isAutoFieldKey(key: string): boolean {
  return AUTO_KEY_RE.test(key)
}

/** 批量注入自动字段：先清掉既有自动字段键（含旧单位变体），再按当前 units 重算并入。
 * 保留用户自定义字段（非自动键）。编辑已建图层时单位变更会把旧后缀列替换为新后缀列。 */
export function injectAutoFields(
  features: GeoJSON.Feature[],
  units: AutoUnitConfig = {},
): GeoJSON.Feature[] {
  return features.map((feature) => {
    const props: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(feature.properties ?? {})) {
      if (!isAutoFieldKey(key)) props[key] = value
    }
    Object.assign(props, computeAutoFields(feature, units))
    return { ...feature, properties: props }
  })
}
