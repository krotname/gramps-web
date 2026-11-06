import {describe, expect, it} from 'vitest'
import maplibregl from '../../src/maplibre.js'
import '@openhistoricalmap/maplibre-gl-dates'

describe('map engine integration', () => {
  it('uses a versioned worker beside the frontend bundle', () => {
    const worker = new URL(maplibregl.getWorkerUrl())
    expect(worker.pathname.endsWith('/maplibre-gl-worker.mjs')).toBe(true)
    expect(worker.searchParams.get('v')).toBe(maplibregl.getVersion())
  })

  it('keeps historical-date filtering on the ESM Map constructor', () => {
    expect(maplibregl.Map.prototype.filterByDate).toBeTypeOf('function')
  })

  it('keeps untrusted popup labels as text', () => {
    const popup = new maplibregl.Popup({closeButton: false})
    const label = '<img onerror="alert(1)" onload="alert(2)" src="x">'
    popup.setText(label)
    expect(popup._content.querySelector('img')).toBeNull()
    expect(popup._content.textContent).toBe(label)
  })
})
