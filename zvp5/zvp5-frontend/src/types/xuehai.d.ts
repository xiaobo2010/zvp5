export {}

declare global {
  interface Window {
    xhBrowserJava?: { getUserId?: () => number | string | null | undefined }
  }
}
