<script setup>
import { useState, useUserStore } from '@/stores'
import { ArrowRight } from '@element-plus/icons-vue'
import { ref, onMounted, computed, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import ZActivityDisplay from '@/components/activity/ZActivityDisplay.vue'
import {
    ElDivider,
    ElButton,
    ElIcon,
    ElRow,
    ElCol,
    ElProgress,
    ElSwitch,
    ElTabs,
    ElTabPane,
    ElPageHeader
} from 'element-plus'

const state = useState()

const user = useUserStore()
const router = useRouter()

const loading = ref(false)

const time = reactive({
  socialPractice: NaN,
  onCampus: NaN,
  offCampus: NaN,
  loaded: false,
})

const convert = ref(state.convert)

const colors = [
    {
        color: 'var(--el-color-danger)',
        percentage: 25,
    },
    {
        color: 'var(--el-color-primary)',
        percentage: 25.01,
    },
    {
        color: 'var(--el-color-primary)',
        percentage: 99.99,
    },
    {
        color: 'var(--el-color-success)',
        percentage: 100,
    },
]

const clamp = x => Math.min(x, 100)
const doConvert = (raw, other, threshold, rate) => raw + Number((Math.max(other - threshold, 0) / rate).toFixed(1))

watch(convert, () => state.set('convert', convert.value))

const onCampus = computed(() => convert.value ? doConvert(time.onCampus, time.offCampus, 15, 2) : time.onCampus)
const offCampus = computed(() => convert.value ? doConvert(time.offCampus, time.onCampus, 25, 3) : time.offCampus)
</script>

<template>
    <div class="home-panel">
        
            <ElDivider content-position="left">
                <span class="section-title">义工时间</span>
            </ElDivider>
            <div class="time-panel" :data-loaded="String(time.loaded)" v-loading="!time.loaded">
                <ElRow>
                    <ElCol class="time-slot" :span="6" :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
                        <ElProgress type="dashboard" :percentage="clamp(onCampus / 25 * 100)" :color="colors">
                            <p class="text-2xl time">{{ onCampus }}h</p>
                            <p class="text-sm">校内义工</p>
                        </ElProgress>
                    </ElCol>
                    <ElCol class="time-slot" :span="6" :xs="12" :sm="12" :md="6" :lg="6" :xl="6">
                        <ElProgress type="dashboard" :percentage="clamp(offCampus / 15 * 100)" :color="colors">
                            <p class="text-2xl time">{{ offCampus }}h</p>
                            <p class="text-sm">校外义工</p>
                        </ElProgress>
                    </ElCol>
                    <ElCol class="hidden-sm-and-down time-slot" :span="6" :xs="0" :sm="0" :md="6" :lg="6" :xl="6">
                        <ElProgress type="dashboard" :percentage="clamp(time.socialPractice / 18 * 100)" :color="colors">
                            <p class="text-2xl time">{{ time.socialPractice }}h</p>
                            <p class="text-sm">社会实践</p>
                        </ElProgress>
                    </ElCol>
                    <ElCol class="hidden-sm-and-down conv-slot" :span="6" :xs="0" :sm="0" :md="6" :lg="6" :xl="6">
                        <span>显示折算</span>
                        <ElSwitch v-model="convert"></ElSwitch>
                    </ElCol>
                </ElRow>
                <ElRow class="hidden-md-and-up">
                    <ElCol class="time-slot" :span="12">
                        <ElProgress type="dashboard" :percentage="clamp(time.socialPractice / 18 * 100)" :color="colors">
                            <p class="text-2xl time">{{ time.socialPractice }}h</p>
                            <p class="text-sm">社会实践</p>
                        </ElProgress>
                    </ElCol>
                    <ElCol class="conv-slot" :span="12">
                        <span>显示折算</span>
                        <ElSwitch v-model="convert"></ElSwitch>
                    </ElCol>
                </ElRow>
            </div>
        
    </div>
</template>

<style scoped>
.fill {
  width: 100%;
}

.z-blur {
  filter: blur(2rem);
}

.home-panel {
    display: flex;
    flex-direction: column;
    padding: 2rem;
    background: var(--el-bg-color);
    gap: .5rem;
}

.home-greeting {
    font-size: x-large;
    font-weight: bold;
    flex-shrink: 0;
    margin-bottom: 1.5rem;
}

.section-title {
    font-size: large;
    font-weight: bold;
}

.time-panel, .todo-panel {
    display: flex;
    flex-direction: column;
}

.todo-panel {
}

.todo-panel .activity {
    padding: .5rem;
    flex-shrink: 0;
    gap: .75em;
    display: flex;
    flex-direction: row;
    overflow: hidden;
    align-items: center;
}

.todo-panel .activity .activity-icon {
    flex-shrink: 0;
}

.todo-panel .activity .activity-inner {
    display: flex;
    flex-direction: column;
    flex-grow: 1;
    overflow: hidden;
}

.todo-panel .activity .activity-name {
    overflow: hidden;
    text-overflow: ellipsis;
}

.todo-panel .activity .activity-info {
    color: var(--el-text-color-secondary);
    font-size: small;
    gap: 1em;
}

.todo-panel .activity .activity-info > *{
    gap: .5em;
}

.todo-panel .activity .activity-info, .todo-panel .activity .activity-info > * {
    display: flex;
    flex-direction: row;
    align-items: center;
}

.todo-panel .activity + .activity {
    border-top: var(--el-border);
}

.time-panel{
    gap: 2rem;
    margin-top: 1.5rem;
    margin-bottom: 1.5rem;
}

.time-slot, .conv-slot {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: .5rem;
}

.time-panel .time {
    transition: opacity .5s ease-in-out;
}

.time-panel:not([data-loaded="true"]) .time {
    opacity: 0;
}

</style>
