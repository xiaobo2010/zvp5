import { defineStore } from 'pinia'

export const useNavStore = defineStore('nav', {
  state: () => ({
    data: '{}',
  }),
  actions: {
    get(key: string, default_: string = '') {
        return JSON.parse(this.data)[key]??default_
    },
    set(key: string, val: string) {
        const data=JSON.parse(this.data)
        data[key]=val
        this.data=JSON.stringify(data)
    },
  },
  persist: {
      storage: sessionStorage,
  }
})
