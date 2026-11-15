declare module '@/vendor/liquid-glass/container.js' {
  export interface ContainerOptions {
    borderRadius?: number
    type?: 'rounded' | 'circle' | 'pill'
    tintOpacity?: number
  }

  export class Container {
    static instances: Container[]
    static pageSnapshot: string | null
    constructor(options?: ContainerOptions)
    element: HTMLElement
    canvas: HTMLCanvasElement | null
    gl_refs?: { gl: WebGLRenderingContext | null }
    addChild(child: { element: HTMLElement }): void
    removeChild(child: { element: HTMLElement }): void
    updateSizeFromDOM(): void
    capturePageSnapshot(): void
    initWebGL(): void
  }
}
