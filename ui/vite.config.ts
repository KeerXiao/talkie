import { defineConfig } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

// Everything is inlined into one index.html. pywebview loads the page over
// file://, where WebKit blocks ES-module and asset fetches by CORS — a single
// self-contained document sidesteps that entirely.
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: '../src/talkie/ui/web',
    emptyOutDir: true,
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
  },
})
