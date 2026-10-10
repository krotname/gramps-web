import {describe, it, expect, vi} from 'vitest'
import {GrampsjsMasterEditor} from '../../src/components/GrampsjsMasterEditor.js'

function editor() {
  const e = new GrampsjsMasterEditor()
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
    e.record = {xref: 'F1', parents: ['I1'], children: ['I2']}
    e.form = {parents: ['I1'], children: ['I2', 'I3']}
    expect(e.operation()).to.deep.equal({
      op: 'family',
      xref: 'F1',
      children: ['I2', 'I3'],
    })
  })
})
