import { ref } from 'vue'

/**
 * 乐观更新：先把界面改成「成功后的样子」，请求失败再回滚。
 * 用法：
 *   const { pending, run } = useOptimistic()
 *   await run({
 *     apply: () => list.value = list.value.filter(x => x.id !== id),
 *     rollback: () => list.value = snapshot,
 *     submit: () => api.remove(id)
 *   })
 */
export function useOptimistic() {
  const pending = ref(false)

  async function run<T>(options: {
    apply: () => void
    rollback: () => void
    submit: () => Promise<T>
    onError?: (error: unknown) => void
  }): Promise<T | undefined> {
    if (pending.value) {
      return undefined
    }
    pending.value = true
    options.apply()
    try {
      return await options.submit()
    } catch (error) {
      options.rollback()
      options.onError?.(error)
      return undefined
    } finally {
      pending.value = false
    }
  }

  return { pending, run }
}
