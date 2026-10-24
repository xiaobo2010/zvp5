<script setup lang="ts">
//import { ArrowRight } from '@element-plus/icons-vue'
import {
    ElConfigProvider,
    ElContainer,
    ElAlert,
    //ElIcon,
    //ElMessageBox
} from 'element-plus'
import { RouterView } from 'vue-router'
import { useUserStore, useState } from '@/stores'
import { useRouter, useRoute } from 'vue-router'
import { useDark, useOnline } from '@vueuse/core'
import { watch, ref, onMounted, h, reactive } from 'vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn.mjs'
import { useRegisterSW } from 'virtual:pwa-register/vue'
import { CarbonCloudOffline } from '@/icons'
//import { isXuehaiDCenter, pad } from '@/plugins/ua.ts'
//import { ICON_XUEHAI_DCENTER, ICON_XUEHAI_BROWSER, XUEHAI_DCENTER_MIN_VERSION } from '@/utils/constants.ts'
import ZNavigation from '@/layout/ZNavigation.vue'

const { needRefresh, offlineReady, updateServiceWorker } = useRegisterSW()

const online = useOnline()
const cached = ref(localStorage.getItem('cached'))
const state = useState()

state.set('unsaved', false)

window.onbeforeunload = (e) => {
  if (state.unsaved) {
    e.preventDefault()
    return false
  }
}

window.oncontextmenu = (e) => {
  e.preventDefault()
  return false
}

const langPack = ref(zhCn)

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// force insecure sessions to log out
const SEC_SEQ = 6
const security = ref(Number(localStorage.getItem('security') || '0') - 0)

if (security.value < SEC_SEQ) {
  security.value = SEC_SEQ
  localStorage.setItem('security', '' + security.value)
  if(userStore.isLogin) {
    userStore.removeUser()
    route.path === '/' || router.replace('/user/login')
  }
}

const dark = useDark()

watch(
  () => userStore.isLogin,
  () => {
    if (!userStore.isLogin && !route.path.endsWith('login') && !route.path.endsWith('about')) {
      router.replace('/user/login')
    }
  }
)


watch(needRefresh, () => {
  if (!needRefresh.value) {
    return
  }
  updateServiceWorker()
})

watch(offlineReady, () => {
  if (!offlineReady.value) {
    return
  }
  localStorage.setItem('cached','true')
})

onMounted(() => {
    /*if(!isXuehaiDCenter(true)) {
        if(isXuehaiDCenter(false)) {
            ElMessageBox({
                title: '应用版本过低',
                message: h(
                    'div',
                    {
                        style: {
                            display: 'flex',
                            'flex-direction': 'column',
                            'align-items': 'center',
                            gap: '.75rem',
                        }
                    },
                    [
                        h(
                            'img',
                            {
                                src: ICON_XUEHAI_DCENTER,
                                style: {
                                    width: '128px',
                                    height: '128px',
                                    'flex-shrink': '0',
                                }
                            },
                        ),
                        h(
                            'span',
                            {
                                style: {
                                    'flex-shrink': '0',
                                    'align-self': 'stretch',
                                    'text-align': 'ceter',
                                }
                            },
                            `资料中心APP版本过低，请联系学海老师升级`,
                        ),
                        h(
                            'span',
                            {
                                style: {
                                    'flex-shrink': '0',
                                    color:'var(--el-text-color-secondary)',
                                    'align-self': 'stretch',
                                    'text-align': 'center',
                                }
                            },
                            `建议使用${XUEHAI_DCENTER_MIN_VERSION}及以上版本`,
                        ),
                    ]
                ),
                center: true,
                confirmButtonText: '我知道了',
                confirmButtonClass: 'el-button--info is-text full-button',
                showClose: false,
                closeOnClickModal: false,
            })
        } else if(pad()) {
            ElMessageBox({
                title: '应用将不再受支持',
                message: h(
                    'div',
                    {
                        style: {
                            display: 'flex',
                            'flex-direction': 'column',
                            'align-items': 'center',
                            gap: '.75rem',
                        }
                    },
                    [
                        h(
                            'div',
                            {
                                style: {
                                    display: 'flex',
                                    'flex-direction': 'row',
                                    'align-items': 'center',
                                    gap: '1rem',
                                }
                            },
                            [
                                h(
                                    'img',
                                    {
                                        src: ICON_XUEHAI_BROWSER,
                                        style: {
                                            width: '112px',
                                            height: '112px',
                                            'flex-shrink': '0',
                                        }
                                    }
                                ),
                                h(
                                    ArrowRight,
                                    {
                                        style: {
                                            width: '48px',
                                            height: '48px',
                                            'flex-shrink': '0',
                                            color: 'var(--el-text-color-placeholder)',
                                        }
                                    }
                                ),
                                h(
                                    'img',
                                    {
                                        src: ICON_XUEHAI_DCENTER,
                                        style: {
                                            width: '128px',
                                            height: '128px',
                                            'flex-shrink': '0',
                                        }
                                    }
                                ),
                            ]
                        ),
                        h(
                            'span',
                            {
                                style: {
                                    'flex-shrink': '0',
                                    'align-self': 'stretch',
                                    'text-align': 'center',
                                }
                            },
                            '学海浏览器将不再受到支持，请使用资料中心APP访问',
                        ),
                        h(
                            'span',
                            {
                                style: {
                                    color: 'var(--el-text-color-secondary)',
                                    'flex-shrink': '0',
                                    'align-self': 'stretch',
                                    'text-align': 'center',
                                }
                            }
                        ),
                    ]
                ),
                center: true,
                confirmButtonText: '我知道了',
                confirmButtonClass: 'el-button--info is-text full-button',
                showClose: false,
                closeOnClickModal: false,
            })
        }
    }*/
})
</script>

<template>
  <ElConfigProvider :locale="langPack" class="bg full" style="overflow: hidden;">
      <ElAlert
        type="error"
        center
        :closable="true"
        v-if="(cached || offlineReady) && !online"
        class="hidden-print"
        style="position: fixed; left: 1rem; top: 1rem; right: 1rem; z-index: 100000; width: auto;"
      >
        <template #title>
          <ElIcon class="disconnected">
            <CarbonCloudOffline></CarbonCloudOffline>
          </ElIcon>
          <span class="text-lg px-1">请检查网络连接</span>
        </template>
      </ElAlert>
      <ElContainer
        @contextmenu.prevent
        class="bg full"
        direction="vertical"
        style="overflow: hidden;"
      >
        <ElContainer class="full" style="overflow: hidden;">
          <ZNavigation vertical></ZNavigation>
          <RouterView
            class="bg-slate-50 dark:bg-gray-950 view fragment-container"
            style="width: 100%; height: 100%; box-sizing: border-box;"
            v-slot="{ Component: SlotComponent }"
          >
            <Transition name="el-fade-in" mode="out-in">
                <Component
                  :is="SlotComponent"
                  class="bg view fragment-container"
                  style="width: 100%; height: 100%; box-sizing: border-box;"
                ></Component>
            </Transition>
          </RouterView>
        </ElContainer>
        <ZNavigation></ZNavigation>
      </ElContainer>
  </ElConfigProvider>
</template>

<style scoped>
.bg {
    background: var(--el-bg-color);
}

.full {
  width: 100%;
  height: 100%;
  flex-grow: 1;
}

.disconnected {
  position: relative;
  top: 40%;
  transform: translateY(16%);
  font-size: 1.2rem;
}

.fragment-container {
  height: 100%;
  overflow-y: scroll;
  flex-grow: 1;
}

.action-btn {
  width: 100%;
}

.view {
  width: 100%;
}

.user {
  position: absolute;
  right: 0;
}
</style>

<style>

.el-pagination {
  overflow-x: scroll;
  overflow-y: hidden !important;
  flex-shrink: 0 !important;
  align-items: flex-end;
}

input::-webkit-outer-spin-button,
input::-webkit-inner-spin-button {
  -webkit-appearance: none;
}

input[type='number'] {
  -moz-appearance: textfield;
}

div[data-netlify-site-id] {
  display: none !important;
}

*,
*:active,
*:focus {
  -webkit-tap-highlight-color: rgba(0, 0, 0, 0) !important;
  -webkit-focus-ring-color: rgba(0, 0, 0, 0) !important;
  outline: none !important;
  -ms-overflow-style: none !important;
  scrollbar-width: 0 !important;
  scrollbar-width: none !important;
}

/*.z-wrap .el-form-item--default .el-form-item__content {
  display: block;
  overflow-wrap: break-word;
}*/

@media print {
  body {
    font-size: 12px;
  }
}

.full-button {
    align-self: stretch;
    flex-grow: 1;
    width: 100%;
}
</style>
