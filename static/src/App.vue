<template>
  <component :is="layout">
    <router-view :key="$route.fullPath" />
  </component>
  <Toast />
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AuthLayout from '@/layouts/AuthLayout.vue'
import AppLayout from '@/layouts/AppLayout.vue'
import { useThemeStore } from '@/stores/theme'
import Toast from '@/components/ui/Toast.vue'

const route = useRoute()
const theme = useThemeStore()
theme.init()

const layout = computed(() =>
  route.name === 'login' || route.name === 'register' ? AuthLayout : AppLayout
)
</script>
