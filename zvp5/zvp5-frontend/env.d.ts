/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute HTTPS API URL ending in /api; required for production builds. */
  readonly VITE_API_BASE?: string
  /** Optional cross-origin static asset base; defaults to same-origin `/`. */
  readonly VITE_STATIC_BASE?: string
  /** Public page origin used to anchor PWA scope when the manifest is cross-origin. */
  readonly VITE_PAGE_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module 'virtual:pwa-register/vue' {
  // eslint-disable-next-line @typescript-eslint/prefer-ts-expect-error
  // @ts-expect-error ignore when vue is not installed
  import type { Ref } from 'vue'
  import type { RegisterSWOptions } from 'vite-plugin-pwa/types'

  export type { RegisterSWOptions }
  export function useRegisterSW(options?: RegisterSWOptions): {
    needRefresh: Ref<boolean>
    offlineReady: Ref<boolean>
    updateServiceWorker: (reloadPage?: boolean) => Promise<void>
  }
}

declare module '~icons/*' {
  import type { FunctionalComponent, SVGAttributes } from 'vue'
  const component: FunctionalComponent<SVGAttributes>
  export default component
}
