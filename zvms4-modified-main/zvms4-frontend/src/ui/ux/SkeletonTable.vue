<script setup lang="ts">
/**
 * 表格骨架屏：数据没到时铺一组扫光条，代替 el-loading 遮罩。
 *
 * 用法：`<SkeletonTable v-if="loading" :rows="6" />`，表格本体放在 `v-else` 里。
 * 不用遮罩的原因见「Liquid Glass 材质规范」§16：遮罩会把已经渲染出来的内容
 * 压暗一层，而骨架屏告诉用户"这里将出现什么"。
 *
 * 每行按列分块（而不是一条通栏灰条），这样读起来像表格而不是列表。
 */
withDefaults(
  defineProps<{
    /** 数据行数量：跟表格一屏能放下的行数对齐，避免加载完成后高度跳动 */
    rows?: number
    /** 首行是否画成表头 */
    header?: boolean
    /** 列宽比例，和真实表格的列结构大致对应；默认五列 */
    cols?: number[]
  }>(),
  { rows: 5, header: true }
)
</script>

<template>
  <div class="yg2-skeleton-table" aria-hidden="true">
    <div v-if="header" class="yg2-skeleton-table__row yg2-skeleton-table__row--head">
      <span
        v-for="(w, i) in cols && cols.length ? cols : [22, 18, 20, 14, 12]"
        :key="i"
        class="yg2-skeleton yg2-skeleton-table__cell"
        :style="{ flex: `0 0 ${w}%` }"
      />
    </div>
    <div v-for="r in rows" :key="r" class="yg2-skeleton-table__row">
      <span
        v-for="(w, i) in cols && cols.length ? cols : [22, 18, 20, 14, 12]"
        :key="i"
        class="yg2-skeleton yg2-skeleton-table__cell"
        :style="{ flex: `0 0 ${w}%` }"
      />
    </div>
  </div>
</template>

<style scoped>
.yg2-skeleton-table {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 6px 0;
}

.yg2-skeleton-table__row {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 46px;
  padding: 0 2px;
}

.yg2-skeleton-table__row--head {
  height: 32px;
}

.yg2-skeleton-table__cell {
  height: 12px;
  border-radius: var(--yg2-r-8);
}

.yg2-skeleton-table__row--head .yg2-skeleton-table__cell {
  height: 10px;
  opacity: 0.72;
}
</style>
