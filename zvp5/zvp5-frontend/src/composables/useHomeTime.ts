// 首页仅在取得有效响应后显示进度；刷新失败保留本次会话的已知数据。
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import type { UserActivityTimeSums } from '@/../types'
import { calculateHomeProgress, type HomeTimeInput } from '@/utils/homeArtwork'

const TIME_KEYS = ['on-campus', 'off-campus', 'social-practice'] as const
const UNKNOWN_TIME_ERROR = '暂时无法取得时长，请重试。'
const REFRESH_TIME_ERROR = '刷新失败，正在显示上次成功取得的时长。'

export function useHomeTime(
  userKey: () => string,
  io: {
    read: (id: string) => Promise<UserActivityTimeSums>
    save: (value: UserActivityTimeSums) => void
  }
) {
  const raw = shallowRef<HomeTimeInput | null>(null)
  const loading = ref(false)
  const error = ref('')
  let sequence = 0
  let disposed = false

  function isTimeSums(value: unknown): value is UserActivityTimeSums {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
    return TIME_KEYS.every((key) => {
      const time = (value as Record<string, unknown>)[key]
      return typeof time === 'number' && Number.isFinite(time) && time >= 0
    })
  }

  async function refresh() {
    const id = userKey()
    const request = ++sequence
    if (disposed) return
    if (!id) {
      loading.value = false
      return
    }

    const current = () => !disposed && request === sequence && userKey() === id
    loading.value = true
    error.value = ''
    try {
      const result: unknown = await io.read(id)
      if (!current()) return
      if (!isTimeSums(result)) throw new Error('Invalid time response')
      raw.value = {
        onCampus: result['on-campus'],
        offCampus: result['off-campus'],
        socialPractice: result['social-practice']
      }
      io.save(result)
    } catch {
      if (current()) error.value = raw.value ? REFRESH_TIME_ERROR : UNKNOWN_TIME_ERROR
    } finally {
      if (current()) loading.value = false
    }
  }

  watch(
    userKey,
    () => {
      sequence++
      raw.value = null
      error.value = ''
      loading.value = false
      void refresh()
    },
    { immediate: true, flush: 'sync' }
  )

  onScopeDispose(() => {
    disposed = true
    sequence++
  })

  const sums = computed<UserActivityTimeSums | undefined>(() =>
    raw.value
      ? {
          'on-campus': raw.value.onCampus,
          'off-campus': raw.value.offCampus,
          'social-practice': raw.value.socialPractice
        }
      : undefined
  )
  const progress = computed(() => (raw.value ? calculateHomeProgress(raw.value) : null))
  return { raw, progress, sums, loading, error, refresh }
}
