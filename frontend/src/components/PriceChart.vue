<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { quotePrice } from '../api'

use([LineChart, GridComponent, TooltipComponent, SVGRenderer])
const props = defineProps({ points: { type: Array, required: true }, unit: { type: String, default: '元/吨' } })
const container = ref(null)
const description = computed(() => `现货价格走势图，共 ${props.points.length} 条真实报价，单位${props.unit}。${props.points.map(point => `${point.quote_date}：${quotePrice(point)}${point.low != null ? `（中值${point.price}）` : ''}`).join('；')}`)
let chart
let observer

function draw() {
  if (!chart) return
  chart.setOption({
    animation: false,
    textStyle: { fontSize: 13 },
    grid: { left: 75, right: 35, top: 40, bottom: 50 },
    tooltip: { textStyle: { fontSize: 14 }, renderMode: 'richText', trigger: 'axis', valueFormatter: (value, index) => `${quotePrice(props.points[index] || { price: value })} ${props.unit}${props.points[index]?.low != null ? `（中值${value}）` : ''}` },
    xAxis: { type: 'category', boundaryGap: true, data: props.points.map(point => point.quote_date), axisLine: { lineStyle: { color: '#dce5df' } }, axisTick: { show: false }, axisLabel: { color: '#7b8881', margin: 18 } },
    yAxis: { type: 'value', name: props.unit, scale: true, nameTextStyle: { color: '#7b8881', align: 'right', padding: [0, 8, 8, 0] }, axisLabel: { color: '#7b8881' }, splitLine: { lineStyle: { color: '#e9eeeb', type: 'dashed' } } },
    series: [{ name: '现货价（区间取中值）', type: 'line', data: props.points.map(point => point.price), smooth: false, symbol: 'circle', symbolSize: props.points.length === 1 ? 12 : 7, showSymbol: true, lineStyle: { width: 3, color: '#257861' }, itemStyle: { color: '#257861', borderColor: '#fff', borderWidth: 2 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: '#25786126' }, { offset: 1, color: '#25786100' }] } } }],
  }, true)
}

onMounted(() => {
  chart = init(container.value, null, { renderer: 'svg' })
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
  draw()
})
watch(() => [props.points, props.unit], draw)
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose(); chart = null })
</script>

<template><div ref="container" class="price-chart" role="img" :aria-label="description"></div></template>
