<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    name: string
    size?: number | string
    strokeWidth?: number
  }>(),
  { size: 20, strokeWidth: 1.65 }
)

const paths: Record<string, string[]> = {
  home: ['M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z'],
  compass: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'm15.5 8.5-2 6-6 2 2-6z'],
  bell: [
    'M18 8a6 6 0 1 0-12 0c0 5-2 6-2 7h16c0-1-2-2-2-7Z',
    'M10 20a2 2 0 0 0 4 0'
  ],
  user: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z', 'M5 20a7 7 0 0 1 14 0'],
  users: [
    'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z',
    'M3 20a6 6 0 0 1 12 0',
    'M16 4.5a3.5 3.5 0 0 1 0 7',
    'M17.5 14a6 6 0 0 1 2.5 6'
  ],
  settings: ['M4 7h10M18 7h2M4 17h4M12 17h8M15 4v6M8 14v6'],
  sparkles: [
    'M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z',
    'M18.5 14l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z'
  ],
  search: ['M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Z', 'm20 20-5-5'],
  sun: [
    'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z',
    'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'
  ],
  moon: ['M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z'],
  logout: ['M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3', 'M10 17l5-5-5-5M15 12H4'],
  'chevron-left': ['M14 6l-6 6 6 6'],
  'chevron-right': ['M10 6l6 6-6 6'],
  'chevron-down': ['M6 10l6 6 6-6'],
  check: ['M4 12.5 9 17.5 20 6.5'],
  x: ['M6 6l12 12M18 6 6 18'],
  alert: [
    'M12 8v5M12 16.5v.5',
    'M10.3 4.3 2.8 18a2 2 0 0 0 1.8 3h14.8a2 2 0 0 0 1.8-3L13.7 4.3a2 2 0 0 0-3.4 0Z'
  ],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z', 'M12 7v5l3 2'],
  pin: [
    'M12 21s7-5.4 7-11a7 7 0 1 0-14 0c0 5.6 7 11 7 11Z',
    'M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z'
  ],
  'arrow-right': ['M4 12h16M14 6l6 6-6 6'],
  'arrow-up-right': ['M7 17 17 7M8 7h9v9'],
  plus: ['M12 5v14M5 12h14'],
  refresh: ['M20 12a8 8 0 1 1-2.3-5.7', 'M20 4v5h-5'],
  server: ['M4 5h16v6H4zM4 13h16v6H4z', 'M8 8h.01M8 16h.01'],
  book: ['M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z', 'M8 7h6M8 11h6'],
  menu: ['M4 7h16M4 12h16M4 17h16'],
  more: ['M6 12h.01M12 12h.01M18 12h.01'],
  shield: ['M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  eye: ['M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6Z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
  'eye-off': [
    'M4 4l16 16',
    'M10.6 6.2A10.6 10.6 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-3 3.5M6.5 8.1A17 17 0 0 0 2 12s3.6 6 10 6c1.5 0 2.8-.3 4-.8'
  ]
}

const content = computed(() => paths[props.name] ?? [])
const dimension = computed(() =>
  typeof props.size === 'number' ? `${props.size}px` : props.size
)
</script>

<template>
  <svg
    :width="dimension"
    :height="dimension"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in content" :key="index" :d="d" />
  </svg>
</template>
