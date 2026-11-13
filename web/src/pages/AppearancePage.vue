<script setup>
import { computed, ref } from 'vue'
import { store, currentTheme, setTheme } from '../lib/store'
import { CATEGORY_LABELS, BUILTIN_THEMES, systemPrefersDark } from '../lib/themes'
import { t } from '../lib/i18n'
import Icon from '../components/Icon.vue'

const query = ref('')
const category = ref('all')
const onlyDark = ref(false)
const applying = ref('')

const categories = computed(() => {
  const seen = new Map()
  for (const t of store.themes) {
    const key = t.category || 'modern'
    seen.set(key, (seen.get(key) || 0) + 1)
  }
  return [...seen.entries()].map(([key, n]) => ({
    key,
    label: CATEGORY_LABELS[key] || key,
    n,
  }))
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return store.themes.filter((t) => {
    if (category.value !== 'all' && (t.category || 'modern') !== category.value) return false
    if (onlyDark.value && !t.dark) return false
    if (!q) return true
    return (
      (t.name || '').toLowerCase().includes(q) ||
      (t.nameEn || '').toLowerCase().includes(q) ||
      (t.slug || '').toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q)
    )
  })
})

const active = computed(() => currentTheme())

function previewStyle(t) {
  const p = t.palette || {}
  const shape = t.shape || {}
  return {
    background: p.bg,
    color: p.text,
    borderColor: p.border,
    borderWidth: `${shape.borderWidth ?? 1}px`,
    borderRadius: `${shape.radius ?? 12}px`,
    boxShadow: shape.radius === 0 ? p.shadow : 'none',
  }
}

function dotStyle(t, i) {
  const list = t.accents && t.accents.length ? t.accents : [t.palette?.accent]
  return { background: list[i % list.length] }
}

async function pick(t) {
  applying.value = t.slug
  try {
    await setTheme(t.slug)
  } finally {
    applying.value = ''
  }
}

</script>

<template>
  <div class="page">
    <div class="page-head">
      <div class="row">
        <div class="grow">
          <h1>{{ t('外观') }}</h1>
          <p>
            共 {{ store.themes.length }} 套配色（内置 {{ 4 }} + StyleKit 151）。
            点击任意卡片立即生效，选择会保存在本机。
          </p>
        </div>
        <span class="chip active">当前：{{ active.name }}</span>
      </div>
    </div>

    <div class="row wrap" style="gap: 10px; margin-bottom: 16px">
      <div class="search-box" style="max-width: 320px">
        <Icon name="search" :size="14" />
        <input v-model="query" :placeholder="t('搜索外观名称 / 英文 / 描述…')" />
      </div>
      <button class="chip" :class="{ active: category === 'all' }" @click="category = 'all'">
        {{ t('全部') }} {{ store.themes.length }}
      </button>
      <button
        v-for="c in categories"
        :key="c.key"
        class="chip"
        :class="{ active: category === c.key }"
        @click="category = c.key"
      >
        {{ t(c.label) }} {{ c.n }}
      </button>
      <button class="chip" :class="{ active: onlyDark }" @click="onlyDark = !onlyDark">
        {{ t('只看深色') }}
      </button>
    </div>

    <div class="theme-cards">
      <button
        class="theme-card-lg system-card"
        :class="{ active: active.slug === 'system' || store.themeSlug === 'system' }"
        :style="{
          background: systemPrefersDark() ? '#0d0f14' : '#f2f4f8',
          color: systemPrefersDark() ? '#e8ebf3' : '#1d2433',
          borderColor: systemPrefersDark() ? '#242a39' : '#dfe4ee',
        }"
        @click="pick({ slug: 'system' })"
      >
        <span class="preview" style="display: flex; flex-direction: row; align-items: center; justify-content: center; gap: 8px">
          <span class="sys-half" style="background: #f2f4f8" />
          <span class="sys-half" style="background: #0d0f14" />
        </span>
        <span class="meta">
          <span class="name">跟随系统</span>
          <span class="en" style="color: currentTheme().palette?.textDim">根据系统深浅色自动切换</span>
        </span>
      </button>
      <button
        v-for="t in filtered"
        :key="t.slug"
        class="theme-card-lg"
        :class="{ active: t.slug === active.slug, applying: applying === t.slug }"
        :style="previewStyle(t)"
        @click="pick(t)"
        :title="t.description || t.name"
      >
        <span class="preview" :style="{ background: t.palette?.bg }">
          <i class="p-panel" :style="{ background: t.palette?.panel, borderColor: t.palette?.border }" />
          <i class="p-line" :style="{ background: t.palette?.textDim }" />
          <i class="p-line short" :style="{ background: t.palette?.textFaint }" />
          <i class="p-accent" :style="{ background: t.palette?.accent }" />
        </span>

        <span class="meta">
          <span class="name">{{ t.name }}</span>
          <span class="en" :style="{ color: t.palette?.textDim }">{{ t.nameEn }}</span>
        </span>

        <span class="dots">
          <i v-for="i in 4" :key="i" :style="dotStyle(t, i - 1)" />
        </span>

        <span v-if="t.slug === active.slug" class="picked"><Icon name="check" :size="12" /></span>
        <span v-if="t.dark" class="dark-tag">暗</span>
      </button>
    </div>

    <div v-if="!filtered.length" class="empty" style="height: 240px">
      <div>
        <div class="icon"><Icon name="search" :size="22" /></div>
        <h3>没有匹配的外观</h3>
        <p>换个关键词，或者清掉分类筛选。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.theme-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: 12px;
}
.theme-card-lg {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border-style: solid;
  cursor: pointer;
  text-align: left;
  color: var(--text);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
  overflow: hidden;
}
.theme-card-lg:hover { transform: translateY(-2px); }
.theme-card-lg.active {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.theme-card-lg.applying { opacity: 0.6; }

.preview {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px;
  border-radius: inherit;
  height: 64px;
  position: relative;
}
.p-panel {
  display: block;
  height: 26px;
  border: 1px solid;
  border-radius: inherit;
  width: 70%;
}
.p-line { display: block; height: 3px; width: 46%; border-radius: 99px; opacity: 0.7; }
.p-line.short { width: 30%; opacity: 0.5; }
.p-accent {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
}

.meta { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.meta .name {
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.meta .en {
  font-size: 10.5px;
  color: var(--text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dots { display: flex; gap: 4px; }
.dots i { width: 10px; height: 10px; border-radius: 3px; display: block; }

.picked {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
}
.dark-tag {
  position: absolute;
  top: 6px;
  left: 6px;
  font-size: 9.5px;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
}
</style>
