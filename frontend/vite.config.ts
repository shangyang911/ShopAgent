import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// [前端构建配置] 启用 Vue 单文件组件转换；其余开发服务器选项使用 Vite 默认值。
export default defineConfig({
  plugins: [vue()],
})
