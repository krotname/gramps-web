import {describe, it, expect, vi} from 'vitest'
import {render} from 'lit'
import en from '../../lang/en.json'
import ru from '../../lang/ru.json'
import {GrampsjsMasterEditor} from '../../src/components/GrampsjsMasterEditor.js'

// happy-dom lacks the form internals used by Material controls.
// Browser acceptance covers their behavior; these tests cover editor state.
vi.mock('@material/web/textfield/outlined-text-field.js', () => ({}))
vi.mock('@material/web/select/outlined-select.js', () => ({}))
vi.mock('@material/web/select/select-option.js', () => ({}))
vi.mock('@material/web/button/filled-button.js', () => ({}))

function editor() {
  const e = new GrampsjsMasterEditor()
  e.appState = {i18n: {strings: en}}
  e.kind = 'person'
  e.record = {
    xref: 'I1',
    name: 'Anna /Old/',
    sex: 'F',
    occupation: '',
    birth: {date: 'ABT 1900', place: 'Old city'},
  }
  e.form = {
    given: 'Anna',
    surname: 'Old',
    sex: 'F',
    occupation: '',
    birthDate: 'ABT 1900',
    birthPlace: 'Old city',
  }
  return e
}

describe('canonical tree editor', () => {
  it('downloads GEDCOM without triggering publication', () => {
    const e = editor()
    e.appState.permissions = {canEditMaster: true}
    const publish = vi.spyOn(e, 'publish').mockImplementation(() => {})
    const download = vi.spyOn(e, 'download').mockImplementation(() => {})
    const target = document.createElement('div')
    render(e.render(), target)
    const button = [...target.querySelectorAll('md-filled-button')].find(b =>
      b.textContent.includes('Download GEDCOM')
    )
    button.click()
    expect(download).toHaveBeenCalledTimes(1)
    expect(publish).not.toHaveBeenCalled()
  })
  for (const [strings, title, action] of [
    [en, 'Tree editor and family books', 'Update Geni and books'],
    [ru, 'Редактор дерева и семейные книги', 'Обновить Geni и книги'],
  ]) {
    it(`renders the editor in the selected language: ${title}`, () => {
      const e = editor()
      e.appState = {i18n: {strings}, permissions: {canEditMaster: true}}
      const target = document.createElement('div')
      render(e.render(), target)
      expect(target.querySelector('summary').textContent).to.equal(title)
      expect(target.textContent).to.contain(action)
    })
  }
  it('adds a father without changing the existing mother role', () => {
    const e = editor()
    e.kind = 'family'
    e.record = {xref: 'F1', parent_roles: {WIFE: 'I1'}, children: ['I2']}
    e.form = {parent_roles: {HUSB: 'I3', WIFE: 'I1'}, children: ['I2']}
    expect(e.operation()).to.deep.equal({
      op: 'family',
      xref: 'F1',
      parent_roles: {HUSB: 'I3', WIFE: 'I1'},
    })
  })
  for (const action of ['save', 'retry']) {
    for (const projection of ['pending', 'ready']) {
      it(`announces database changes only after a ready projection on ${action} (${projection})`, async () => {
        const e = editor()
        e.status = {revision: 'old', projection: 'ready'}
        const changed = vi.fn()
        window.addEventListener('db:changed', changed)
        vi.spyOn(e, 'post').mockResolvedValue({projection})
        vi.spyOn(e, 'refresh').mockResolvedValue()
        vi.spyOn(e, 'load').mockResolvedValue()
        try {
          await e[action]({preventDefault() {}})
          expect(changed).toHaveBeenCalledTimes(projection === 'ready' ? 1 : 0)
        } finally {
          window.removeEventListener('db:changed', changed)
        }
      })
    }
  }
  it('changes only the selected place and preserves a partial date', () => {
    const e = editor()
    e.form.birthPlace = 'New city'
    expect(e.operation()).to.deep.equal({
      op: 'person',
      xref: 'I1',
      birth: {place: 'New city'},
    })
  })
  it('keeps given names and surname separate when editing', () => {
    const e = editor()
    e.form.given = 'Anna Maria'
    expect(e.operation().name).to.equal('Anna Maria /Old/')
  })
  it('preserves typed changes when a revision conflict prevents saving', async () => {
    const e = editor()
    e.status = {revision: 'old', projection: 'ready'}
    e.form.birthPlace = 'New city'
    vi.spyOn(e, 'post').mockRejectedValue(
      new Error('The tree changed; reload before saving')
    )
    await e.save({preventDefault() {}})
    expect(e.form.birthPlace).to.equal('New city')
    expect(e.message).to.contain('tree changed')
    expect(e.busy).to.be.false
  })
  it('changes children without rewriting the unchanged parents', () => {
    const e = editor()
    e.kind = 'family'
    e.record = {xref: 'F1', parent_roles: {WIFE: 'I1'}, children: ['I2']}
    e.form = {parent_roles: {WIFE: 'I1'}, children: ['I2', 'I3']}
    expect(e.operation()).to.deep.equal({
      op: 'family',
      xref: 'F1',
      children: ['I2', 'I3'],
    })
  })
  it('uses the shared mutation wrapper and suppresses premature database notifications', async () => {
    const e = editor()
    e.appState.apiPost = vi
      .fn()
      .mockResolvedValue({data: {projection: 'pending'}})
    const payload = {revision: 'current'}
    expect(await e.post('/api/master/commit/', payload)).to.deep.equal({
      projection: 'pending',
    })
    expect(e.appState.apiPost).toHaveBeenCalledWith(
      '/api/master/commit/',
      payload,
      {dbChanged: false}
    )
  })
  it('can load again after a transient initial request failure', async () => {
    const e = editor()
    e.appState.apiGet = vi.fn().mockResolvedValueOnce({error: 'offline'})
    await e.refresh()
    expect(e.loaded).not.to.be.true
    e.appState.apiGet = vi.fn().mockResolvedValue({data: {books: []}})
    await e.refresh()
    expect(e.loaded).to.be.true
  })
})
