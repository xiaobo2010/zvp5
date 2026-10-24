<script setup lang="ts">
/**
 * ZPagination —— 统一翻页组件
 *
 * 设计要点（贴合使用习惯）：
 * - 响应式布局：宽屏显示 总数/每页条数/页码/跳页，窄屏（<768px）只保留 上一页/页码/下一页
 * - 页码数量合法：Element Plus 要求 pager-count 为 ≥5 的奇数（旧代码传 3 会渲染异常）
 * - 切换每页条数时自动回到第 1 页，避免停留在不存在的页码
 * - 居中排列、自动换行，移动端不再横向溢出滚动
 * - 触控友好：窄屏使用 small 尺寸 + 更大的点击热区（样式见下方）
 */
import { computed } from 'vue'
import { ElPagination } from 'element-plus'
import { useWindowSize } from '@vueuse/core'

const props = withDefaults(
  defineProps<{
    /** 总条数 */
    total: number
    /** 当前页（v-model:current-page） */
    currentPage: number
    /** 每页条数（v-model:page-size） */
    pageSize: number
    /** 可选每页条数 */
    pageSizes?: number[]
    /** 只有一页时是否隐藏 */
    hideOnSinglePage?: boolean
  }>(),
  {
    pageSizes: () => [10, 20, 50],
    hideOnSinglePage: false
  }
)

const emit = defineEmits<{
  'update:currentPage': [page: number]
  'update:pageSize': [size: number]
  change: [page: number, size: number]
}>()

const { width } = useWindowSize()
const isMobile = computed(() => width.value < 768)

// 窄屏极简布局；宽屏完整布局
const layout = computed(() =>
  isMobile.value ? 'prev, pager, next' : 'total, sizes, prev, pager, next, jumper'
)
// pager-count 必须为 ≥5 的奇数
const pagerCount = computed(() => (isMobile.value ? 5 : 7))

function onPageChange(page: number) {
  emit('update:currentPage', page)
  emit('change', page, props.pageSize)
}

function onSizeChange(size: number) {
  emit('update:pageSize', size)
  // 换每页条数后回到第 1 页，符合直觉，也避免页码越界出现空列表
  emit('update:currentPage', 1)
  emit('change', 1, size)
}
</script>

<template>
  <div class="yg-pagination-bar">
    <ElPagination
      :current-page="currentPage"
      :page-size="pageSize"
      :total="total"
      :page-sizes="pageSizes"
      :pager-count="pagerCount"
      :layout="layout"
      :small="isMobile"
      :hide-on-single-page="hideOnSinglePage"
      background
      @current-change="onPageChange"
      @size-change="onSizeChange"
    />
  </div>
</template>

<style scoped>
.yg-pagination-bar {
  display: flex;
  justify-content: center;
  width: 100%;
  padding: 12px 0;
}

/* 允许换行居中，杜绝横向溢出滚动 */
.yg-pagination-bar :deep(.el-pagination) {
  flex-wrap: wrap;
  justify-content: center;
  row-gap: 8px;
  white-space: normal;
}

/* 触控友好：加大按钮点击热区 */
.yg-pagination-bar :deep(.el-pagination button),
.yg-pagination-bar :deep(.el-pagination .el-pager li) {
  min-width: 32px;
  height: 32px;
  line-height: 32px;
}
</style>
