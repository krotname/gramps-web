import * as maplibregl from 'maplibre-gl'

const workerUrl = new URL('./maplibre-gl-worker.mjs', import.meta.url)
workerUrl.searchParams.set('v', maplibregl.getVersion())
maplibregl.setWorkerUrl(workerUrl.toString())

// Legacy plugins extend this mutable global; ESM namespace objects are sealed.
window.maplibregl = {...maplibregl}

export default maplibregl
