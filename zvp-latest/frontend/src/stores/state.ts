import { defineStore } from 'pinia'

export const useState = defineStore('state', {
    state: () => ({
        newui: false,
        convert: true,
        unsaved: false,
    }),
    actions: {
        toggleNewUI() {
            this.newui = !this.newui
        },
        set(key, val) {
            const k = String(key)
            if (k !== 'newui') this[k] = val
        },
    },
    persist: {
        storage: localStorage,
    },
})
