import { defineStore } from 'pinia'
import type { Group } from '@/../types'
import api from '@/api'

// 失败结果短 TTL 负缓存：读群组失败（403/404/网络）时同一 id 在 TTL 内直接返回失败，
// 不再重复发请求。否则展示同一群组的组件每次重新挂载都会重发同一个必然失败的请求
// （2026-10 曾观察到用户页每秒 1 次 403 请求风暴）。
const FAILURE_TTL = 30_000

export const useGroupsStore = defineStore('group', {
  state: () => ({
    groups: {} as Record<string, Group>,
    failedAt: {} as Record<string, number>
  }),
  actions: {
    async fetchGroup(id: string) {
      if (id in this.groups) {
        return this.groups[id]
      }

      const failedAt = this.failedAt[id]
      if (failedAt !== undefined) {
        if (Date.now() - failedAt < FAILURE_TTL) {
          return undefined
        }
        delete this.failedAt[id]
      }

      let group: Group | undefined
      try {
        group = await api.group.readOne(id)
      } catch {
        this.failedAt[id] = Date.now()
        return undefined
      }

      if (group) {
        this.groups[id] = group
        return group
      }

      this.failedAt[id] = Date.now()
      return undefined
    }
  }
})
