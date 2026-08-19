import { useEffect, useRef, useState, type ReactNode } from 'react'
import { LIQUID_ORB_SHADER, LIQUID_ORB_UNIFORM_SEED } from './liquidOrbShader'

interface LiquidOrbProps {
  /** 球直径（px） */
  size: number
  /** WebGPU 不可用 / 初始化失败时的替代内容（默认无） */
  fallback?: ReactNode
  className?: string
  title?: string
}

/**
 * 液态玻璃球（Glass Liquid）：WebGPU 渲染的流动玻璃球。
 * - 每实例独立 device + 渲染循环（小尺寸多实例开销可控，rAF 在后台标签页会被浏览器暂停）；
 * - 无 WebGPU（Safari / Firefox 等）或初始化失败 → 渲染 fallback（各调用处回退原 Bot 图标）；
 * - uniform 布局与逐帧写入对齐 liquid-orb.html：values[0/1]=canvas 像素尺寸、values[2]=时间。
 */
export function LiquidOrb({ size, fallback = null, className, title }: LiquidOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [supported, setSupported] = useState<boolean>(
    () => typeof navigator !== 'undefined' && !!(navigator as { gpu?: unknown }).gpu,
  )

  useEffect(() => {
    if (!supported) return
    let cancelled = false
    const canvas = canvasRef.current
    if (!canvas) return

    let device: any = null
    let raf = 0

    const init = async () => {
      try {
        const gpu = (navigator as any).gpu
        const adapter = await gpu.requestAdapter()
        if (cancelled) return
        if (!adapter) throw new Error('no adapter')
        const dev = await adapter.requestDevice()
        if (cancelled) {
          dev.destroy()
          return
        }
        device = dev
        const ctx = canvas.getContext('webgpu') as any
        if (!ctx) throw new Error('no webgpu context')

        const format = gpu.getPreferredCanvasFormat()
        ctx.configure({ device: dev, format, alphaMode: 'premultiplied' })

        const shader = dev.createShaderModule({ code: LIQUID_ORB_SHADER })
        const info = await shader.getCompilationInfo()
        if (cancelled) return
        if (info.messages.some((m: any) => m.type === 'error')) {
          throw new Error(info.messages.map((m: any) => `${m.lineNum}:${m.linePos} ${m.message}`).join('\n'))
        }

        const pipeline = dev.createRenderPipeline({
          layout: 'auto',
          vertex: { module: shader, entryPoint: 'vs_main' },
          fragment: { module: shader, entryPoint: 'fs_main', targets: [{ format }] },
          primitive: { topology: 'triangle-list' },
        })

        // 0x40 = GPUBufferUsage.UNIFORM，0x8 = COPY_DST
        const values = new Float32Array(LIQUID_ORB_UNIFORM_SEED)
        const uniformBuffer = dev.createBuffer({
          size: values.byteLength,
          usage: 0x40 | 0x8,
        })
        const bindGroup = dev.createBindGroup({
          layout: pipeline.getBindGroupLayout(0),
          entries: [{ binding: 0, resource: { buffer: uniformBuffer } }],
        })
        const startedAt = performance.now()

        const frame = (now: number) => {
          if (cancelled) return
          raf = requestAnimationFrame(frame)
          const dpr = Math.min(window.devicePixelRatio || 1, 2)
          const w = Math.max(1, Math.floor(canvas.clientWidth * dpr))
          const h = Math.max(1, Math.floor(canvas.clientHeight * dpr))
          if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w
            canvas.height = h
          }
          values[0] = w
          values[1] = h
          values[2] = (now - startedAt) / 1000
          dev.queue.writeBuffer(uniformBuffer, 0, values)

          const encoder = dev.createCommandEncoder()
          const pass = encoder.beginRenderPass({
            colorAttachments: [
              {
                view: ctx.getCurrentTexture().createView(),
                clearValue: { r: 0, g: 0, b: 0, a: 0 },
                loadOp: 'clear',
                storeOp: 'store',
              },
            ],
          })
          pass.setPipeline(pipeline)
          pass.setBindGroup(0, bindGroup)
          pass.draw(3)
          pass.end()
          dev.queue.submit([encoder.finish()])
        }
        raf = requestAnimationFrame(frame)
      } catch {
        if (!cancelled) setSupported(false)
      }
    }
    void init()

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      device?.destroy()
    }
  }, [supported])

  if (!supported) return <>{fallback}</>

  return (
    <canvas
      ref={canvasRef}
      className={className ? `liquid-orb ${className}` : 'liquid-orb'}
      title={title}
      aria-label={title}
      style={{ width: size, height: size }}
    />
  )
}
