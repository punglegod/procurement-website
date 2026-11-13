<script setup>
import { computed, ref } from 'vue'
import { useMarket } from '../store'
import { direction, number, signed, sourceUrl, statusText } from '../api'

const { items, loading, quoted, stale, refresh } = useMarket()
const query = ref('')
const filter = ref('all')
const filtered = computed(() => items.value.filter(item =>
  `${item.name} ${item.code}`.toLowerCase().includes(query.value.trim().toLowerCase()) &&
  (filter.value === 'all' || (filter.value === 'quoted' ? item.price_point : !item.price_point)),
))
</script>

<template>
  <h1 class="page-title">行情总览</h1>

  <section class="summary-grid" aria-label="行情概况">
    <article class="summary-card summary-primary"><span>跟踪品种</span><div class="summary-value">{{ items.length || '—' }}<small>个</small></div></article>
    <article class="summary-card"><span>已有报价</span><div class="summary-value">{{ quoted }}<small>/ {{ items.length || '—' }}</small></div></article>
    <article class="summary-card"><span>过期报价</span><div class="summary-value" :class="{ 'text-amber': stale }">{{ stale }}<small>个</small></div><p>{{ stale ? '黄色标记 · 请留意报价日期' : '已有报价中暂无过期数据' }}</p></article>
    <article class="summary-card"><span>暂无报价</span><div class="summary-value">{{ items.length - quoted }}<small>个</small></div></article>
  </section>

  <section class="market-panel">
    <div class="panel-toolbar"><div class="panel-title"><h2>现货报价</h2><span class="count-badge">{{ items.length }} 个品种</span></div><div class="toolbar-actions"><button class="button" :disabled="loading" @click="refresh">{{ loading ? '读取中…' : '刷新数据' }}</button><label class="search-field"><span aria-hidden="true">⌕</span><input v-model="query" aria-label="搜索品种" placeholder="搜索品种 / 代码" type="search" /></label></div></div>
    <div class="table-tools"><div class="filter-tabs" aria-label="行情筛选"><button :class="{ selected: filter === 'all' }" :aria-pressed="filter === 'all'" @click="filter = 'all'">全部品种</button><button :class="{ selected: filter === 'quoted' }" :aria-pressed="filter === 'quoted'" @click="filter = 'quoted'">已有报价</button><button :class="{ selected: filter === 'pending' }" :aria-pressed="filter === 'pending'" @click="filter = 'pending'">暂无报价</button></div><span class="table-legend">涨跌对比上一报价日 · 同一口径</span></div>
    <div class="table-scroll" tabindex="0" aria-label="品种报价表，可横向滚动">
      <table class="price-table">
        <thead><tr><th scope="col">品种</th><th scope="col" class="numeric">最新价<small>元/吨</small></th><th scope="col" class="numeric">涨跌额</th><th scope="col" class="numeric">涨跌幅</th><th scope="col">报价口径</th><th scope="col">报价日期</th><th scope="col">来源</th><th scope="col"><span class="sr-only">详情</span></th></tr></thead>
        <tbody>
          <tr v-for="item in filtered" :key="item.code" :class="{ 'stale-row': item.price_point && item.stale }">
            <th scope="row"><RouterLink :to="`/commodity/${item.code}`" class="commodity-link">{{ item.name }}</RouterLink><div class="row-meta"><span class="code-label">{{ item.code.toUpperCase() }}</span><span class="status-badge" :class="item.price_point ? (item.stale ? 'badge-amber' : 'badge-green') : 'badge-muted'">{{ statusText(item) }}</span></div></th>
            <td class="numeric price-cell">{{ number(item.price_point?.price) }}<small v-if="item.price_point">{{ item.price_point.unit }}</small></td>
            <td class="numeric" :class="direction(item.change_amount)" :title="item.previous_quote_date ? `对比 ${item.previous_quote_date}` : '暂无上一报价日数据'">{{ signed(item.change_amount) }}</td>
            <td class="numeric" :class="direction(item.change_percent)">{{ signed(item.change_percent, '%') }}</td>
            <td class="basis-cell">{{ item.price_point?.quote_basis || item.quote_basis }}<small v-if="!item.price_point">{{ item.message }}</small></td>
            <td class="date-cell">{{ item.price_point?.quote_date || '—' }}<small v-if="item.price_point && item.stale" class="text-amber">过期 · 非今日报价</small></td>
            <td class="source-cell"><a v-if="item.price_point && sourceUrl(item.price_point.source_url)" :href="sourceUrl(item.price_point.source_url)" target="_blank" rel="noopener noreferrer">{{ item.price_point.source_name }} <span aria-hidden="true">↗</span></a><span v-else class="muted">{{ item.enabled === false ? '免费源待接入' : '暂无入库报价' }}</span><small v-if="item.price_point && ['failed', 'blocked', 'interrupted'].includes(item.data_status)" class="text-amber" :title="item.message">来源异常 · 保留已有报价</small></td>
            <td><RouterLink class="detail-link" :to="`/commodity/${item.code}`" :aria-label="`查看${item.name}走势`">↗</RouterLink></td>
          </tr>
          <tr v-if="!filtered.length"><td colspan="8" class="empty-cell">{{ loading ? '正在读取入库行情…' : items.length ? '没有符合条件的品种，请调整搜索或筛选。' : '暂无行情，请检查服务连接。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="table-footer"><span>显示 {{ filtered.length }} / {{ items.length }} 个品种</span><span>“—” 表示暂无报价或缺少上一报价日数据</span></div>
  </section>
  <div class="notice info-notice"><span class="info-icon" aria-hidden="true">i</span><p>11 个品种均使用免费公开现货源。各品种规格与计价方式见报价口径；请留意报价日期。点击品种查看历史走势，点击来源查看原始报价。</p></div>
</template>
