<script setup>
import { toRefs, ref, watch } from 'vue'
import { HomeFilled, UserFilled, List, Management } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/stores'
import { useNavStore } from '@/stores/nav'
import { ElButton } from 'element-plus'

const props = defineProps({
    vertical: { type: Boolean, default: false }
})

const { vertical } = toRefs(props)

const user = useUserStore()
const navStore = useNavStore()

const navs = [
    {
        path: '/user',
        name: '首页',
        icon: HomeFilled,
        visible: () => true
    },
    {
        path: '/activities',
        name: '义工',
        icon: List,
        visible: () => true
    },
    {
        path: '/manage',
        name: '管理',
        icon: Management,
        visible: () => user.position.includes('admin') || user.position.includes('department') || user.position.includes('secretary') && !!user.class_id
    },
    {
        path: '/me',
        name: '我的',
        icon: UserFilled,
        visible: () => true
    }
]

const navPaths = navs.map((x,i)=>{
    x.fullPath = navStore.get(i, x.path)
    return x.path
})

const route = useRoute()

watch(() => route.fullPath, p => {
    const path = route.path
    const idx = navPaths.indexOf(path)
    if (idx >= 0) {
        navs[idx].fullPath = p
        navStore.set(idx, p)
    }
})
</script>

<template>
  <Transition name="el-fade-in" mode="out-in">
    <div
        v-if="navPaths.includes($route.path)"
        :class="vertical ? [
            'z-navigation',
            'is-vertical',
            'flex-col',
        ] : [
            'z-navigation',
            'is-horizontal',
            'flex-row',
        ]"
    >
        <template
            v-for="nav in navs"
            :key="nav.path"
        >
            <ElButton
                v-if="nav.visible()"
                :type="$route.path == nav.path ? 'primary' : ''"
                text
                :bg="vertical && $route.path == nav.path"
                 round
                :icon="nav.icon"
                @click="nav.path == $route.path || $router.replace(nav.fullPath ?? nav.path)"
            >
                {{nav.name}}
            </ElButton>
        </template>
    </div>
    <div v-else class="dummy"></div>
  </Transition>
</template>

<style>
.z-navigation {
    display: none;
}

@media only screen and (orientation: landscape) {
    .z-navigation.is-vertical {
        display: flex;
    }
}

@media only screen and (orientation: portrait) {
    .z-navigation.is-horizontal {
        display: flex;
    }
}

.z-navigation {
    position: relative;
    z-index: 1;
    box-shadow: var(--el-box-shadow-light);
    background: var(--el-bg-color);
    flex-shrink: 0;
}

.z-navigation.is-vertical {
    padding:.5rem;
    gap: .5rem;
    justify-content: center;
} 

.z-navigation > .el-button {
    margin: 0 !important;
}

.z-navigation.is-horizontal > .el-button {
    flex-grow: 1;
    border-radius: 0;
    display: flex;
    flex-direction: column;
    padding: .5rem;
    height: auto;
    gap: .25rem;
}


.z-navigation.is-vertical > .el-button {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: .25rem;
    height: auto;
}

.z-navigation > .el-button > * {
    flex-shrink: 0;
    margin: 0 !important;
}

.z-navigation.is-horizontal > .el-button > .el-icon {
    font-size: x-large;
}

.z-navigation.is-vertical > .el-button > .el-icon {
    font-size: large;
}

.dummy {
    position: fixed;
    left: 0;
    top: 0;
    opacity: 0;
    pointer-events: none;
    user-select: none;
}
</style>