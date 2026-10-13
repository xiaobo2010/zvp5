import axios, { AxiosError } from 'axios'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import nprogress from 'nprogress'
import 'nprogress/nprogress.css'
import router from '@/router'
import { useUserStore } from '@/stores/user'
import { cacheGet, cacheSet } from '@/utils/idbCache'
import { getXuehaiId } from '@/plugins/ua'

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

function cacheKey(config: InternalAxiosRequestConfig) {
  const params = config.params ? JSON.stringify(config.params) : ''
  return `${config.baseURL ?? ''}${config.url ?? ''}${params}`
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
      const status = error.response?.status
      const transportFailed = error.response === undefined
      if (config && isCacheableGet(config) && (transportFailed || status === 502 || status === 503 || status === 504)) {
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