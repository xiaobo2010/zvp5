import { computed, reactive, ref, watch } from 'vue'
import api from '@/api'
import { useUserStore } from '@/stores/user'

/**
 * 义工时长读取 + 折算。
 *
 * 折算规则（与后端口径一致，也是「折算公示」里展示的那套）：
 *   校内超出 25h 的部分，按 3h 折 1h 计入校外；
 *   校外超出 15h 的部分，按 2h 折 1h 计入校内。
 */
export const TIME_STANDARD = {
  'on-campus': 25,
  'off-campus': 15,
  'social-practice': 18
} as const

export type TimeKey = keyof typeof TIME_STANDARD

function discountOf(real: number, standard: number, rate: number) {
  return real > standard ? Math.round((real - standard) / rate) : 0
}

export function useUserTime(userId: () => string) {
  const userStore = useUserStore()
  const loading = ref(true)
  const error = ref('')
  const exceed = ref(false)

  const base = reactive<Record<TimeKey, number>>({
    'on-campus': 0,
    'off-campus': 0,
    'social-practice': 0
  })

  const canRead = computed(
    () =>
      userId() === userStore._id ||
      userStore.position.includes('admin') ||
      userStore.position.includes('department') ||
      userStore.position.includes('secretary')
  )

  /** 折算后用于展示的三个值 */
  const shown = computed<Record<TimeKey, number>>(() => ({
    'on-campus':
      base['on-campus'] + (exceed.value ? discountOf(base['off-campus'], 15, 2) : 0),
    'off-campus':
      base['off-campus'] + (exceed.value ? discountOf(base['on-campus'], 25, 3) : 0),
    'social-practice': base['social-practice']
  }))

  const total = computed(
    () => shown.value['on-campus'] + shown.value['off-campus'] + shown.value['social-practice']
  )

  async function refresh() {
    const id = userId()
    if (!id) {
      loading.value = false
      return
    }
    if (!canRead.value) {
      error.value = '无权限查看该用户时长'
      loading.value = false
      return
    }
    loading.value = true
    error.value = ''
    try {
      const result = await api.user.time.read(id)
      if (result) {
        base['on-campus'] = result['on-campus']
        base['off-campus'] = result['off-campus']
        base['social-practice'] = result['social-practice']
        if (id === userStore._id) {
          userStore.setTime(result)
        }
      }
    } catch {
      error.value = '时长数据加载失败，请稍后重试'
    } finally {
      loading.value = false
    }
  }

  watch(userId, () => void refresh(), { immediate: true })

  return { loading, error, exceed, base, shown, total, refresh, canRead }
}
