import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import nprogress from 'nprogress'
import 'nprogress/nprogress.css'

// Function to get the value of a specific cookie
function getCookieValue(cookieName: string) {
  const cookies = document.cookie.split('; ')
  for (const cookie of cookies) {
    const [name, value] = cookie.split('=')
    if (name === cookieName) {
      return value
    }
  }
  return null
}

export const baseURL = '/api/'

const axiosInstance = axios.create({
  baseURL,
  withCredentials: true,
  timeout: 12000,
  headers: {
    'Content-type': 'application/json',
    'Xuehai-ID': String(window.xhBrowserJava?.getUserId?.() ?? ''),
    'Device-ID': localStorage.getItem('deviceId') || '',
  }
})

// Request Interceptor

axiosInstance.interceptors.request.use(
  (config) => {
    if(config.nprogress === false) {
        delete config.nprogress
    } else {
        nprogress.start()
        // ElMessage({message:config.url})
    }
    const token = localStorage.getItem('token')
    if (token && !config.headers['Authorization']) {
      config.headers['Authorization'] = `Bearer ${token}`
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
    return response
  },
  (error: Error | AxiosError) => {
    nprogress.done()
    let errorDisplayed = false
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401) {
        if (!errorDisplayed) {
          errorDisplayed = true
          const token = localStorage.getItem('token')
          ElMessage({
            message: error.response?.data?.detail,
            type: 'error',
            grouping: true,
            plain: true
          })
          if (token) {
            localStorage.removeItem('token')
          }
          (import('@/stores/user')).then(({useUserStore}) => useUserStore()
            .removeUser()
            .then(() => import('@/router'))
            .then(router => {
              router.default.replace('/user/login')
            })
          )
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
    return Promise.reject(error)
  }
)

export default axiosInstance
