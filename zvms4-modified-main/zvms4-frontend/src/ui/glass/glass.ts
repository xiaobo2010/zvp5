/**
 * Liquid Glass · WebGL 折射引擎（干净重写，学习自 dashersw/liquid-glass-js）
 *
 * 与上游的差别（详见 .liquid-glass-lab/NOTES.md）：
 * 1) 采样源解耦：`setSource()` 接受任意 canvas，不绑定 html2canvas、不做 toDataURL 往返；
 * 2) 所有长度参数都是 CSS 像素，DPR 只在 canvas 尺寸与 u_dpr 两处出现；
 * 3) 折射是单层 `edge²` 指数衰减（refract = 最大偏移 px，edgeWidth = 衰减宽度 px），
 *    替代上游 base/edge/rim 三层强度 × 三层距离六个互相纠缠的参数；
 * 4) 高光用「迎光弧」而不是 3D 法线点乘 —— 后者在圆角法线突变处会打出白色尖刺；
 * 5) 按需渲染：没有常驻 rAF。尺寸变化只置脏，调用方在需要时 render()。
 */

export type GlassShape = 'rounded' | 'circle' | 'pill'

export interface GlassOptions {
  /** 形状；默认 rounded */
  shape?: GlassShape
  /** rounded 用的圆角半径（CSS px） */
  radius?: number
  /** 高斯模糊：σ = blur / 2（CSS px） */
  blur?: number
  /** 乳白不透明度，0..1 */
  tint?: number
  /** 边缘最大折射偏移（CSS px） */
  refract?: number
  /** 折射衰减宽度（CSS px） */
  edgeWidth?: number
  /** 迎光弧强度，0..1 */
  spec?: number
  /** 色散偏移（CSS px） */
  aberration?: number
  /** 颗粒幅度 */
  grain?: number
  /** 渲染倍率，默认 min(devicePixelRatio, 2) */
  scale?: number
  /** true = 每次 render() 都重新上传纹理（采样源会变、或采样父级 WebGL canvas 时必须开） */
  live?: boolean
  /** 保留 drawing buffer —— 被下层玻璃当作采样源时必须开 */
  preserve?: boolean
}

export interface SourceMeta {
  /** 纹理左上角在「页面坐标」里的位置（= 视口坐标 + window.scrollX/Y） */
  origin?: { x: number; y: number }
  /** 纹理覆盖的 CSS 宽度；缺省按 canvas.width / scale 推断 */
  width?: number
}

interface ResolvedOptions extends Required<Omit<GlassOptions, 'preserve' | 'live'>> {
  live: boolean
  preserve: boolean
}

const VERTEX_SHADER = /* glsl */ `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`

const FRAGMENT_SHADER = /* glsl */ `
precision highp float;

uniform sampler2D u_tex;
uniform vec2  u_cssSize;     // 元素尺寸（CSS px）
uniform vec2  u_texSize;     // 纹理尺寸（纹素）
uniform vec2  u_origin;      // 元素左上角在纹理中的位置（纹素）
uniform float u_scale;       // 纹素 / CSS px
uniform float u_dpr;         // 画布像素 / CSS px
uniform int   u_shape;       // 0 rounded, 1 circle, 2 pill
uniform float u_radius;
uniform float u_blur;
uniform float u_tint;
uniform float u_refract;
uniform float u_edgeWidth;
uniform float u_spec;
uniform float u_aberration;
uniform float u_grain;

float sdShape(vec2 p) {
  vec2 b = u_cssSize * 0.5;
  if (u_shape == 1) return length(p) - min(b.x, b.y);
  if (u_shape == 2) {
    float r = b.y;
    float halfLen = max(b.x - r, 0.0);
    vec2 q = vec2(max(abs(p.x) - halfLen, 0.0), p.y);
    return length(q) - r;
  }
  float r = clamp(u_radius, 0.0, min(b.x, b.y));
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

vec2 sdGrad(vec2 p) {
  float e = 0.75;
  vec2 g = vec2(
    sdShape(p + vec2(e, 0.0)) - sdShape(p - vec2(e, 0.0)),
    sdShape(p + vec2(0.0, e)) - sdShape(p - vec2(0.0, e))
  );
  float l = length(g);
  return l > 0.0 ? g / l : vec2(0.0, -1.0);
}

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
  vec2 dev = gl_FragCoord.xy;
  vec2 fragDown = vec2(dev.x, u_cssSize.y * u_dpr - dev.y) / u_dpr;
  vec2 p = fragDown - u_cssSize * 0.5;

  float sd = sdShape(p);
  float alpha = smoothstep(0.7, -0.7, sd);
  if (alpha <= 0.0) { gl_FragColor = vec4(0.0); return; }

  vec2 n = sdGrad(p);
  float d = max(-sd, 0.0);
  float edge = exp(-d / max(u_edgeWidth, 0.001));
  float edge2 = edge * edge;

  // 折射：边缘外侧的内容被折进来
  vec2 offCss = n * (edge2 * u_refract);
  vec2 basePx = u_origin + (fragDown + offCss) * u_scale;
  vec2 uv = basePx / u_texSize;

  // 高斯模糊（7×7 圆核，σ = blur/2），三通道各自带色散偏移
  float sigma = max(u_blur * 0.5 * u_scale, 0.0);
  float stepPx = max(sigma * 0.5, 1.0);
  vec2 texel = vec2(stepPx) / u_texSize;
  vec2 ca = n * (edge2 * u_aberration) / u_texSize;

  vec3 col = vec3(0.0);
  float wsum = 0.0;
  for (int i = -3; i <= 3; i++) {
    for (int j = -3; j <= 3; j++) {
      float r2 = float(i * i + j * j);
      if (r2 > 10.0) continue;
      float w = exp(-r2 / 8.0);
      vec2 tuv = uv + vec2(float(i), float(j)) * texel;
      col += vec3(texture2D(u_tex, tuv + ca).r,
                  texture2D(u_tex, tuv).g,
                  texture2D(u_tex, tuv - ca).b) * w;
      wsum += w;
    }
  }
  col /= wsum;

  // 鲜艳度 + 乳白 tint（顶部更白，模拟顶部受光）
  float luma = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(vec3(luma), col, 1.25);
  float top = 1.0 - fragDown.y / max(u_cssSize.y, 1.0);
  col = mix(col, vec3(1.0), u_tint * (0.75 + 0.25 * top));
  col += vec3(0.045) * smoothstep(0.0, 0.55, top);

  // 迎光弧：贴边柔化带 × 法线与光向的夹角
  float rimBand = 1.0 - smoothstep(0.0, 1.0 + u_edgeWidth * 0.55, d);
  float dir = max(dot(n, normalize(vec2(-0.45, -0.89))), 0.0);
  col += vec3(pow(dir, 1.6) * rimBand * u_spec * 0.85);

  // 1px 内圈亮线，按元素尺寸缩放（小元素不至于像贴纸描边）
  float lineK = clamp(min(u_cssSize.x, u_cssSize.y) / 90.0, 0.3, 1.0);
  float line = smoothstep(1.8, 0.0, abs(d - 1.0));
  col += vec3(line * 0.13 * lineK);

  col += (hash(fragDown) - 0.5) * u_grain;

  gl_FragColor = vec4(col, alpha);
}
`

const UNIFORMS = [
  'u_tex',
  'u_cssSize',
  'u_texSize',
  'u_origin',
  'u_scale',
  'u_dpr',
  'u_shape',
  'u_radius',
  'u_blur',
  'u_tint',
  'u_refract',
  'u_edgeWidth',
  'u_spec',
  'u_aberration',
  'u_grain',
] as const

type UniformName = (typeof UNIFORMS)[number]

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)
  if (!shader) throw new Error('LiquidGlass: 无法创建 shader')
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader)
    gl.deleteShader(shader)
    throw new Error(`LiquidGlass: shader 编译失败 — ${log ?? 'unknown'}`)
  }
  return shader
}

export class LiquidGlass {
  readonly host: HTMLElement
  readonly canvas: HTMLCanvasElement
  private readonly gl: WebGLRenderingContext
  private readonly uniforms: Record<UniformName, WebGLUniformLocation | null>
  private readonly texture: WebGLTexture
  private readonly observer: ResizeObserver
  private options: ResolvedOptions

  private source: HTMLCanvasElement | null = null
  private sourceOrigin = { x: 0, y: 0 }
  private sourceWidth = 0
  private textureWidth = 0
  private textureHeight = 0
  private dirty = true
  private cssWidth = 0
  private cssHeight = 0
  private destroyed = false

  constructor(host: HTMLElement, options: GlassOptions = {}) {
    this.host = host
    this.options = {
      shape: options.shape ?? 'rounded',
      radius: options.radius ?? 24,
      blur: options.blur ?? 12,
      tint: options.tint ?? 0.14,
      refract: options.refract ?? 12,
      edgeWidth: options.edgeWidth ?? 16,
      spec: options.spec ?? 0.55,
      aberration: options.aberration ?? 1.4,
      grain: options.grain ?? 0.02,
      scale: options.scale ?? Math.min(window.devicePixelRatio || 1, 2),
      live: options.live ?? false,
      preserve: options.preserve ?? false,
    }

    const canvas = document.createElement('canvas')
    canvas.className = 'lg-canvas'
    canvas.style.cssText =
      'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;border-radius:inherit;'

    const gl = canvas.getContext('webgl', {
      alpha: true,
      premultipliedAlpha: false,
      antialias: false,
      preserveDrawingBuffer: this.options.preserve,
      powerPreference: 'low-power',
    })
    if (!gl) throw new Error('LiquidGlass: 当前环境没有可用的 WebGL')

    this.canvas = canvas
    this.gl = gl

    const program = gl.createProgram()
    if (!program) throw new Error('LiquidGlass: 无法创建 program')
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER))
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`LiquidGlass: program 链接失败 — ${gl.getProgramInfoLog(program) ?? ''}`)
    }
    gl.useProgram(program)

    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    )
    const posLoc = gl.getAttribLocation(program, 'a_pos')
    gl.enableVertexAttribArray(posLoc)
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0)

    const uniforms = {} as Record<UniformName, WebGLUniformLocation | null>
    for (const name of UNIFORMS) uniforms[name] = gl.getUniformLocation(program, name)
    this.uniforms = uniforms

    const texture = gl.createTexture()
    if (!texture) throw new Error('LiquidGlass: 无法创建纹理')
    this.texture = texture
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
    gl.uniform1f(uniforms.u_scale, 1)

    // canvas 必须在宿主内容之下，但仍在宿主自己的层叠上下文里
    if (getComputedStyle(host).isolation === 'auto') host.style.isolation = 'isolate'
    canvas.style.zIndex = '-1'
    host.appendChild(canvas)

    this.observer = new ResizeObserver(() => {
      this.dirty = true
      this.resize()
      this.render()
    })
    this.observer.observe(host)
    this.resize()
  }

  /** 更新参数（悬停、主题切换、preset 变化都走这里），不重建 GL 资源 */
  update(patch: Partial<GlassOptions>): void {
    Object.assign(this.options, patch)
    this.dirty = true
  }

  /** 标记纹理需要重新上传（采样源内容变了） */
  invalidate(): void {
    this.dirty = true
  }

  /**
   * 设置采样源。
   * @param source 任意 canvas（页面背景快照 / 父级玻璃 canvas / 视频帧…）
   * @param meta `origin` 是纹理左上角在页面坐标里的位置；离屏 canvas 必须显式给，
   *            否则会按 (0, 0) 处理（等价于「纹理就是整页」）。
   */
  setSource(source: HTMLCanvasElement, meta: SourceMeta = {}): void {
    this.source = source
    this.textureWidth = source.width
    this.textureHeight = source.height
    this.sourceOrigin = meta.origin ?? { x: 0, y: 0 }
    this.sourceWidth =
      meta.width ?? (this.textureWidth > 0 ? this.textureWidth / this.options.scale : 0)
    this.dirty = true
  }

  /** 重新丈量尺寸；尺寸没变就不动 canvas（避免每帧重分配） */
  resize(): void {
    const w = this.host.clientWidth
    const h = this.host.clientHeight
    if (!w || !h) return
    if (w === this.cssWidth && h === this.cssHeight) return
    this.cssWidth = w
    this.cssHeight = h
    const scale = this.options.scale
    this.canvas.width = Math.max(1, Math.round(w * scale))
    this.canvas.height = Math.max(1, Math.round(h * scale))
    this.canvas.style.width = `${w}px`
    this.canvas.style.height = `${h}px`
    this.dirty = true
  }

  render(): void {
    if (this.destroyed) return
    const source = this.source
    if (!source || !this.textureWidth || !this.sourceWidth) return
    this.resize()
    if (!this.cssWidth || !this.cssHeight) return

    const gl = this.gl
    const o = this.options

    if (this.dirty || o.live) {
      gl.bindTexture(gl.TEXTURE_2D, this.texture)
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
      this.dirty = false
    }

    const hostRect = this.host.getBoundingClientRect()
    const texelsPerCss = this.textureWidth / this.sourceWidth
    const originX =
      (hostRect.left + window.scrollX - this.sourceOrigin.x) * texelsPerCss
    const originY =
      (hostRect.top + window.scrollY - this.sourceOrigin.y) * texelsPerCss

    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)

    const u = this.uniforms
    gl.uniform2f(u.u_cssSize, this.cssWidth, this.cssHeight)
    gl.uniform2f(u.u_texSize, this.textureWidth, this.textureHeight)
    gl.uniform2f(u.u_origin, originX, originY)
    gl.uniform1f(u.u_scale, texelsPerCss)
    gl.uniform1f(u.u_dpr, o.scale)
    gl.uniform1i(u.u_shape, o.shape === 'circle' ? 1 : o.shape === 'pill' ? 2 : 0)
    gl.uniform1f(u.u_radius, o.radius)
    gl.uniform1f(u.u_blur, o.blur)
    gl.uniform1f(u.u_tint, o.tint)
    gl.uniform1f(u.u_refract, o.refract)
    gl.uniform1f(u.u_edgeWidth, o.edgeWidth)
    gl.uniform1f(u.u_spec, o.spec)
    gl.uniform1f(u.u_aberration, o.aberration)
    gl.uniform1f(u.u_grain, o.grain)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.texture)
    gl.uniform1i(u.u_tex, 0)
    gl.drawArrays(gl.TRIANGLES, 0, 6)
  }

  destroy(): void {
    if (this.destroyed) return
    this.destroyed = true
    this.observer.disconnect()
    try {
      this.gl.getExtension('WEBGL_lose_context')?.loseContext()
    } catch {
      /* 有些环境不支持 lose_context，忽略即可 */
    }
    this.canvas.remove()
  }
}
