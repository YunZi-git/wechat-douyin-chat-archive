<script setup>
import { onMounted } from 'vue'
import { store, boot } from './lib/store'
import SideRail from './components/SideRail.vue'
import ChatsPage from './pages/ChatsPage.vue'
import VoicePage from './pages/VoicePage.vue'
import MergePage from './pages/MergePage.vue'
import ImportPage from './pages/ImportPage.vue'
import ExportPage from './pages/ExportPage.vue'
import AppearancePage from './pages/AppearancePage.vue'
import SettingsPage from './pages/SettingsPage.vue'

onMounted(boot)
</script>

<template>
  <div class="workbench">
    <SideRail />

    <ChatsPage v-if="store.view === 'chats'" />
    <VoicePage v-else-if="store.view === 'voice'" />
    <MergePage v-else-if="store.view === 'merge'" />
    <ImportPage v-else-if="store.view === 'import'" />
    <ExportPage v-else-if="store.view === 'export'" />
    <AppearancePage v-else-if="store.view === 'appearance'" />
    <SettingsPage v-else-if="store.view === 'settings'" />

    <div v-if="!store.booted" class="boot-mask">
      <div class="spinner" />
    </div>
  </div>

  <div class="toast-wrap">
    <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type">{{ t.message }}</div>
  </div>
</template>

<style>
.boot-mask {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  background: var(--bg);
  z-index: 100;
}
.boot-mask .spinner { width: 26px; height: 26px; border-width: 3px; }
</style>
