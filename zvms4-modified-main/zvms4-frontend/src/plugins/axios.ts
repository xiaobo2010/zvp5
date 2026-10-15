import axios, { AxiosError } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import nprogress from 'nprogress'
import 'nprogress/nprogress.css'
import router from '@/router'
import { useUserStore } from '@/stores/user'
import { cacheGet, cacheSet } from '@/utils/idbCache'
import { getXuehaiId } from '@/plugins/ua'
import { trackRequest, releaseRequest, type TrackedRequestConfig } from '@/plugins/inflight'

const apiBase = import.meta.env.VITE_API_BASE ?? '/api'

export const baseURL = apiBase.replace(/\/+$/, '')

const axiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 12000,
  headers: {
    'Content-type': 'application/json'
  }
})

function isCacheableGet(config?: InternalAxiosRequestConfig) {
  if (!config || config.method !== 'get') {
    return false
  }
  const responseType = config.responseType
  return responseType === undefined || responseType === 'json'
}

function isIdempotentGet(config?: InternalAxiosRequestConfig) {
  return config?.method === 'get'
}

function cacheKey(config: InternalAxiosRequestConfig) {
  const params = config.params ? JSON.stringify(config.params) : ''
  return `${config.baseURL ?? ''}${config.url ?? ''}${params}`
}

const RETRYABLE_STATUS = new Set([502, 503, 504])
const RETRY_BACKOFF_MS = 300

function isRetryableFailure(config: InternalAxiosRequestConfig, error: AxiosError) {
  if (!isIdempotentGet(config)) {
    return false
  }
  const status = error.response?.status
  return error.response === undefined || (status !== undefined && RETRYABLE_STATUS.has(status))
}

function backoff() {
  return new Promise((resolve) => setTimeout(resolve, RETRY_BACKOFF_MS))
}

let lastStaleNotice = 0
function noticeStaleCache() {
  const now = Date.now()
  if (now - lastStaleNotice < 10000) {
    return
  }
  lastStaleNotice = now
  ElMessage({
    message: '网络不稳定，已显示本地缓存数据',
    type: 'warning',
    grouping: true,
    plain: true
  })
}

// Request Interceptor

axiosInstance.interceptors.request.use(
  (config) => {
    nprogress.start()
    const token = localStorage.getItem('token')
    if (token && !config.headers['Authorization']) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    const xuehaiId = getXuehaiId()
    if (xuehaiId > 0 && !config.headers['Xuehai-Id']) {
      config.headers['Xuehai-Id'] = String(xuehaiId)
    }
    trackRequest(config)
    return config
  },
  (error) => {
    nprogress.done()
    return Promise.reject(error)
  }
)

// Response Interceptor

axiosInstance.interceptors.response.use(
  (response) => {
    releaseRequest(response.config)
    nprogress.done()
    if (isCacheableGet(response.config)) {
      void cacheSet(cacheKey(response.config), response.data)
    }
    return response
  },
  async (error: Error | AxiosError) => {
    nprogress.done()
    if (axios.isAxiosError(error)) {
      const config = error.config
      releaseRequest(config)
      if (axios.isCancel(error) || config?.signal?.aborted) {
        return Promise.reject(error)
      }
      if (config && isRetryableFailure(config, error)) {
        // Offline / server-unavailable: prefer local cache before retrying so the
        // two recovery paths never fire for the same failure.
        if (isCacheableGet(config)) {
          const cached = await cacheGet(cacheKey(config))
          if (cached) {
            noticeStaleCache()
            const stale: AxiosResponse = {
              data: cached.data,
              status: 200,
              statusText: 'OK',
              headers: { 'x-zvms-local-cache': String(cached.t) },
              config
            }
            return stale
          }
        }
        const retryConfig = config as TrackedRequestConfig
        if (!retryConfig.__retried) {
          retryConfig.__retried = true
          await backoff()
          return axiosInstance(config)
        }
      }
    }
    let errorDisplayed = false
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401) {
        if (!errorDisplayed) {
          errorDisplayed = true
          const token = localStorage.getItem('token')
          ElMessage({
            message: token ? '登录已过期' : '未登录',
            type: 'error',
            grouping: true,
            plain: true
          })
          if (token) {
            localStorage.removeItem('token')
          }
          useUserStore()
            .removeUser()
            .then(() => {
              router.replace('/user/login')
            })
        }
      } else {
        if (!errorDisplayed) {
          errorDisplayed = true
          ElMessage({
            message: error.response?.data?.detail
              ? '' +
                (typeof error.response?.data?.detail === 'string'
                  ? error.response?.data?.detail
                  : JSON.stringify(error.response?.data?.detail))
              : '未知错误',
            type: 'error',
            grouping: true,
            plain: true
          })
        }
      }
    } else {
      if (!errorDisplayed) {
        errorDisplayed = true
        ElMessage({
          message:
            '错误: ' + error.message
              ? 'data: ' + JSON.stringify(error.message)
              : '未知错误',
          type: 'error',
          grouping: true,
          plain: true
        })
      }
    }
  }
)

export default axiosInstance
