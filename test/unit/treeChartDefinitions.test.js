import {describe, it, expect, vi} from 'vitest'
import {
  chartDataUrl,
  chartDefinitions,
  chartSettingValues,
} from '../../src/views/treeChartDefinitions.js'
import {GrampsjsViewTree} from '../../src/views/GrampsjsViewTree.js'
import {chartNameDisplayFormat} from '../../src/util.js'

const rulesOf = url =>
  JSON.parse(decodeURIComponent(/rules=([^&]*)/.exec(url)[1]))

const extendOf = url => /extend=([^&]*)/.exec(url)[1]

describe('chart definitions', () => {
  it('reads setting values from the user settings, with defaults', () => {
    const values = chartSettingValues(chartDefinitions.hourglass, {
      hourglassChartDesc: 4,
    })
    expect(values).toEqual({
      ancestors: 2,
      descendants: 4,
      nameDisplayFormat: chartNameDisplayFormat.surnameThenGiven,
    })
  })

  it('fetches one more generation than the settings count', () => {
    const {ancestor, descendant, hourglass, fan} = chartDefinitions
    const generations = (definition, settings) =>
      rulesOf(
        chartDataUrl(
          definition,
          'I1',
          chartSettingValues(definition, settings),
          'en'
        )
      ).rules.map(rule => rule.values)
    expect(generations(ancestor, {treeChartAnc: 5})).toEqual([
      ['I1', 6],
      ['I1', 2],
    ])
    expect(generations(descendant, {descendantChartDesc: 3})).toEqual([
      ['I1', 2],
      ['I1', 4],
    ])
    expect(generations(hourglass, {})).toEqual([
      ['I1', 3],
      ['I1', 2],
    ])
    expect(generations(fan, {})).toEqual([
      ['I1', 5],
      ['I1', 2],
    ])
  })

  it('fetches people by degree of separation with all parent families for the relationship chart', () => {
    const {relationship} = chartDefinitions
    const url = chartDataUrl(
      relationship,
      'I1',
      chartSettingValues(relationship, {relationshipChartAnc: 3}),
      'de'
    )
    expect(rulesOf(url).rules).toEqual([
      {name: 'DegreesOfSeparation', values: ['I1', 3]},
    ])
    expect(extendOf(url)).toBe(
      'event_ref_list,primary_parent_family,family_list,parent_family_list'
    )
    expect(url).toContain('locale=de')
  })

  it('only allows adding people to the charts that have cards', () => {
    const editable = Object.entries(chartDefinitions)
      .filter(([, definition]) => definition.editable)
      .map(([name]) => name)
    expect(editable).toEqual([
      'ancestor',
      'descendant',
      'hourglass',
      'relationship',
    ])
  })
})

// Returns a promise with its resolve function
function deferred() {
  let resolvePromise
  const promise = new Promise(resolve => {
    resolvePromise = resolve
  })
  return {promise, resolve: resolvePromise}
}

function makeView(settings = {}) {
  const view = new GrampsjsViewTree()
  const apiGet = vi.fn()
  view.appState = {settings, i18n: {lang: 'en'}, apiGet}
  view.grampsId = 'I1'
  return {view, apiGet}
}

describe('GrampsjsViewTree', () => {
  it('builds relationships when the optional server filter is missing', async () => {
    const {view, apiGet} = makeView({relationshipChartAnc: 1})
    view._currentTabId = 3
    const people = [
      {
        handle: 'R',
        gramps_id: 'I1',
        extended: {
          families: [
            {
              handle: 'F',
              father_handle: 'R',
              mother_handle: 'S',
              child_ref_list: [{ref: 'K'}],
            },
          ],
        },
      },
      {handle: 'S'},
      {handle: 'K'},
      {handle: 'unrelated'},
    ]
    apiGet
      .mockResolvedValueOnce({error: 'Not Found', errorDetail: {status: 404}})
      .mockResolvedValueOnce({data: people})
    await view._fetchData(
      chartDataUrl(view.definition, 'I1', view.settingValues, 'en')
    )
    expect(apiGet).toHaveBeenCalledTimes(2)
    expect(apiGet.mock.calls[1][0]).not.toContain('rules=')
    expect(apiGet.mock.calls[1][0]).toContain('parent_family_list')
    expect(view._data.map(p => p.handle)).toEqual(['R', 'S', 'K'])
    expect(view.error).toBe(false)
    expect(view.loading).toBe(false)
  })

  it('does not replace authorization failures with a relationship fallback', async () => {
    const {view, apiGet} = makeView()
    view._currentTabId = 3
    apiGet.mockResolvedValue({error: 'Forbidden', errorDetail: {status: 403}})
    await view._fetchData('people/?rules=filter')
    expect(apiGet).toHaveBeenCalledOnce()
    expect(view.error).toBe(true)
    expect(view._errorMessage).toBe('Forbidden')
  })

  it('ignores relationship fallback data after the selected person changes', async () => {
    const {view, apiGet} = makeView()
    view._currentTabId = 3
    const fallback = deferred()
    apiGet
      .mockResolvedValueOnce({error: 'Not Found', errorDetail: {status: 404}})
      .mockReturnValueOnce(fallback.promise)
      .mockResolvedValueOnce({data: [{handle: 'new', gramps_id: 'I2'}]})
    const first = view._fetchData('people/?rules=filter')
    await Promise.resolve()
    view.grampsId = 'I2'
    await view._fetchData('people/?rules=new')
    fallback.resolve({data: [{handle: 'old', gramps_id: 'I1'}]})
    await first
    expect(view._data).toEqual([{handle: 'new', gramps_id: 'I2'}])
    expect(view.loading).toBe(false)
  })

  it('fetches again only when the request changes', () => {
    const settings = {}
    const {view, apiGet} = makeView(settings)
    apiGet.mockReturnValue(new Promise(() => {}))
    view._fetchIfNeeded()
    view._fetchIfNeeded()
    expect(apiGet).toHaveBeenCalledTimes(1)
    settings.treeChartNameDisplayFormat = 'Given Surname'
    view._fetchIfNeeded()
    expect(apiGet).toHaveBeenCalledTimes(1)
    settings.treeChartAnc = 6
    view._fetchIfNeeded()
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('records each selected person in the history, also the first one', () => {
    const {view} = makeView()
    // The first person is selected before the view is first updated
    view.willUpdate(new Map())
    view.grampsId = 'I2'
    view.willUpdate(new Map())
    expect(view._history).toEqual(['I1', 'I2'])
    view._prevPerson()
    view.willUpdate(new Map())
    expect(view.grampsId).toBe('I1')
    expect(view._history).toEqual(['I1'])
  })

  it('does not pass people fetched for one chart to another', () => {
    const {view, apiGet} = makeView()
    apiGet.mockReturnValue(new Promise(() => {}))
    view.willUpdate(new Map())
    view._data = [{handle: 'A'}]
    view._fetchIfNeeded()
    view._currentTabId = 3
    view.willUpdate(new Map())
    expect(view._data).toEqual([])
    view._fetchIfNeeded()
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('shows the previous person while the selected person is loading', () => {
    const {view} = makeView()
    view._data = [
      {gramps_id: 'I1', profile: {name_given: 'Ann', name_surname: 'Lee'}},
    ]
    view.willUpdate(new Map())
    view.grampsId = 'I2'
    view.willUpdate(new Map())
    expect(view._selectedPerson.gramps_id).toBe('I1')
    view._data = [{gramps_id: 'I2', profile: {name_given: 'Bo'}}]
    view.willUpdate(new Map())
    expect(view._selectedPerson.gramps_id).toBe('I2')
  })

  it('opens the page of the shown person while the selected person is loading', () => {
    const {view} = makeView()
    view._data = [{gramps_id: 'I1', profile: {name_given: 'Ann'}}]
    view.willUpdate(new Map())
    view.grampsId = 'I2'
    view.willUpdate(new Map())
    const paths = []
    view.addEventListener('nav', e => paths.push(e.detail.path))
    view._goToPerson()
    expect(paths).toEqual(['person/I1'])
  })

  describe('viewport keys', () => {
    const keyEvent = (
      key,
      {target = document.body, path, ...modifiers} = {}
    ) => ({
      key,
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      ...modifiers,
      composedPath: () => path ?? [target],
      preventDefault: vi.fn(),
    })

    function makeKeyView() {
      const {view} = makeView()
      view.active = true
      const viewport = {
        zoomBy: vi.fn(),
        panBy: vi.fn(),
        fit: vi.fn(),
        centreRoot: vi.fn(),
      }
      view._chartViewport = () => viewport
      return {view, viewport}
    }

    it('zooms, moves and fits the chart', () => {
      const {view, viewport} = makeKeyView()
      for (const key of ['+', '=', '-', 'ArrowRight', 'ArrowUp', '0']) {
        view._handleChartKey(keyEvent(key))
      }
      expect(viewport.zoomBy.mock.calls.map(([factor]) => factor)).toEqual([
        1.25, 1.25, 0.8,
      ])
      expect(viewport.panBy.mock.calls.map(([dx, dy]) => [dx, dy])).toEqual([
        [-100, 0],
        [0, 100],
      ])
      expect(viewport.fit).toHaveBeenCalledOnce()
    })

    it('leaves keys with modifiers, in fields, tabs and menus alone', () => {
      const {view, viewport} = makeKeyView()
      const input = document.createElement('input')
      // Only the tag name of the chart switcher matters
      const tabs = {tagName: 'GRAMPSJS-PILL-TOGGLE'}
      const events = [
        keyEvent('+', {ctrlKey: true}),
        keyEvent('-', {metaKey: true}),
        keyEvent('+', {target: input}),
        keyEvent('ArrowLeft', {path: [document.body, tabs]}),
      ]
      for (const event of events) {
        view._handleChartKey(event)
        expect(event.preventDefault).not.toHaveBeenCalled()
      }
      expect(viewport.zoomBy).not.toHaveBeenCalled()
      expect(viewport.panBy).not.toHaveBeenCalled()
    })

    it('leaves keys alone while the view is not active', () => {
      const {view, viewport} = makeKeyView()
      view.active = false
      view._handleChartKey(keyEvent('+'))
      expect(viewport.zoomBy).not.toHaveBeenCalled()
    })

    it('has no viewport for the fan chart', () => {
      const {view} = makeView()
      view._currentTabId = 4
      expect(view._chartViewport()).toBeUndefined()
    })
  })

  it('ignores a response that arrives after a newer request was sent', async () => {
    const {view, apiGet} = makeView()
    const first = deferred()
    const second = deferred()
    apiGet
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    view._fetchIfNeeded()
    view.grampsId = 'I2'
    view._fetchIfNeeded()
    second.resolve({data: [{handle: 'B'}]})
    first.resolve({data: [{handle: 'A'}]})
    await Promise.all([first.promise, second.promise])
    await Promise.resolve()
    expect(view._data).toEqual([{handle: 'B'}])
    expect(view.loading).toBe(false)
  })
})
