import {LitElement, css, html} from 'lit'
import {GrampsjsAppStateMixin} from '../mixins/GrampsjsAppStateMixin.js'
import {__APIHOST__} from '../api.js'
import {fireEvent} from '../util.js'
import '@material/web/textfield/outlined-text-field.js'
import '@material/web/select/outlined-select.js'
import '@material/web/select/select-option.js'
import '@material/web/button/filled-button.js'

export class GrampsjsMasterEditor extends GrampsjsAppStateMixin(LitElement) {
  static get properties() {
    return {
      people: {state: true},
      record: {state: true},
      form: {state: true},
      status: {state: true},
      message: {state: true},
      busy: {state: true},
      books: {state: true},
      kind: {state: true},
      families: {state: true},
      audit: {state: true},
      confirmedRevision: {state: true},
    }
  }

  static get styles() {
    return css`
      :host {
        display: block;
        padding: 12px 24px;
        background: var(--md-sys-color-surface-container, #f5f3ef);
      }
      details {
        max-width: 1100px;
        margin: auto;
      }
      summary {
        cursor: pointer;
        font-weight: 600;
      }
      .fields {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 12px;
        margin: 16px 0;
      }
      label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: 14px;
      }
      md-outlined-text-field,
      md-outlined-select {
        width: 100%;
      }
      md-filled-button {
        margin: 8px 8px 8px 0;
      }
      .message {
        white-space: pre-wrap;
      }
    `
  }

  constructor() {
    super()
    this.people = []
    this.record = null
    this.form = {}
    this.status = {}
    this.message = ''
    this.busy = false
    this.books = []
    this.kind = 'person'
    this.families = []
    this.audit = []
    this.confirmedRevision = null
  }

  updated(changed) {
    if (changed.has('appState') && !this.loaded && this.appState.auth) {
      this.refresh()
    }
  }

  async get(route) {
    const r = await this.appState.apiGet(route)
    if (r.error) throw new Error(r.error)
    return r.data
  }

  async post(route, payload) {
    const r = await this.appState.apiPost(route, payload, {dbChanged: false})
    if (r.error) throw new Error(r.error)
    return r.data
  }

  async refresh() {
    this.confirmedRevision = null
    this.record = null
    this.form = {}
    try {
      this.status = await this.get('/api/master/status/')
      this.people = await this.get('/api/master/people/')
      this.families = await this.get('/api/master/families/')
      const b = await this.get('/api/master/books/')
      this.books = b.books || []
      this.loaded = true
    } catch (e) {
      this.message = e.message
    }
  }

  async approveDemo() {
    if (this.confirmedRevision !== this.status.revision) {
      this.message =
        'Подтвердите, что все данные вымышлены и готовы к открытому показу.'
      return
    }
    this.busy = true
    try {
      await this.post('/api/master/approve-demo/', {
        revision: this.status.revision,
        synthetic_only: true,
      })
      this.message = 'Публичный снимок обновлён. Проверьте открытый пример.'
    } catch (e) {
      this.message = e.message
    } finally {
      this.confirmedRevision = null
      this.busy = false
    }
  }

  async showAudit() {
    try {
      this.audit = await this.get('/api/master/learning-audit/')
    } catch (e) {
      this.message = e.message
    }
  }

  async load(id) {
    if (!id) {
      this.record = null
      return
    }
    try {
      if (this.kind === 'family') {
        this.record = this.families.find(f => f.xref === id)
        this.form = {
          parent_roles: {...this.record.parent_roles},
          children: [...this.record.children],
          note: '',
          source: '',
        }
        return
      }
      this.record = await this.get('/api/master/person/' + id + '/')
      this.status = {...this.status, revision: this.record.revision}
      this.form = {
        given: this.record.given || this.record.name.split('/')[0].trim(),
        surname:
          this.record.surname || this.record.name.split('/')[1]?.trim() || '',
        sex: this.record.sex,
        occupation: this.record.occupation,
        note: '',
        source: '',
      }
      for (const key of ['birth', 'death', 'baptism', 'burial']) {
        this.form[key + 'Date'] = this.record[key]?.date || ''
        this.form[key + 'Place'] = this.record[key]?.place || ''
      }
    } catch (e) {
      this.message = e.message
    }
  }

  field(key, label) {
    return html`<md-outlined-text-field
      label=${this._(label)}
      .value=${this.form[key] || ''}
      @input=${e => {
        this.form = {...this.form, [key]: e.target.value}
      }}
    ></md-outlined-text-field>`
  }

  familyName(family) {
    return (
      Object.values(family.parent_roles || {})
        .map(
          id =>
            this.people.find(p => p.xref === id)?.name.replaceAll('/', '') || id
        )
        .join(', ') || this._('Family without specified parents')
    )
  }

  members(key, label, limit = Infinity) {
    const ids = this.form[key] || []
    const change = (index, value) => {
      const values = [...ids]
      values[index] = value
      this.form = {...this.form, [key]: values.filter(Boolean)}
    }
    return html`<div>
      <p>${this._(label)}</p>
      ${ids.map(
        (id, index) =>
          html`<label
            >${this._('Child')} ${index + 1}<md-outlined-select
              .value=${id}
              @change=${e => change(index, e.target.value)}
            >
              <md-select-option value=""
                ><div slot="headline">
                  ${this._('Remove from family')}
                </div></md-select-option
              >
              ${this.people.map(
                p =>
                  html`<md-select-option value=${p.xref}
                    ><div slot="headline">
                      ${p.name.replaceAll('/', '')}
                    </div></md-select-option
                  >`
              )}
            </md-outlined-select></label
          >`
      )}${ids.length < limit
        ? html`<label
            >${this._('Add')}<md-outlined-select
              .value=${''}
              @change=${e => change(ids.length, e.target.value)}
            >
              <md-select-option value=""
                ><div slot="headline">
                  ${this._('Choose a person')}
                </div></md-select-option
              >
              ${this.people
                .filter(p => !ids.includes(p.xref))
                .map(
                  p =>
                    html`<md-select-option value=${p.xref}
                      ><div slot="headline">
                        ${p.name.replaceAll('/', '')}
                      </div></md-select-option
                    >`
                )}
            </md-outlined-select></label
          >`
        : ''}
    </div>`
  }

  parent(role, label) {
    const roles = this.form.parent_roles || {}
    return html`<label
      >${this._(label)}<md-outlined-select
        .value=${roles[role] || ''}
        @change=${e => {
          this.form = {
            ...this.form,
            parent_roles: {...roles, [role]: e.target.value},
          }
        }}
      >
        <md-select-option value=""
          ><div slot="headline">
            ${this._('Not specified')}
          </div></md-select-option
        >
        ${this.people
          .filter(
            p =>
              p.xref === roles[role] || !Object.values(roles).includes(p.xref)
          )
          .map(
            p =>
              html`<md-select-option value=${p.xref}
                ><div slot="headline">
                  ${p.name.replaceAll('/', '')}
                </div></md-select-option
              >`
          )}
      </md-outlined-select></label
    >`
  }

  stateLabel(state) {
    const keys = {
      pending: 'Pending',
      ready: 'Ready',
      failed: 'Failed',
      blocked: 'Blocked',
    }
    return this._(keys[state] || state)
  }

  newRecord() {
    const id =
      (this.kind === 'person' ? 'ILOCAL' : 'FLOCAL') +
      crypto.randomUUID().replaceAll('-', '')
    this.record = {
      xref: id,
      create: true,
      name: '',
      sex: 'U',
      occupation: '',
      parent_roles: {},
      children: [],
    }
    this.form = {
      given: '',
      surname: '',
      sex: 'U',
      parent_roles: {},
      children: [],
      note: '',
      source: '',
    }
  }

  operation() {
    const r = this.record
    const f = this.form
    const op = {
      op: this.kind,
      xref: r.xref,
      ...(r.create ? {create: true} : {}),
    }
    if (this.kind === 'family') {
      const roles = Object.fromEntries(
        Object.entries(f.parent_roles || {}).filter(([, id]) => id)
      )
      if (
        r.create ||
        ['HUSB', 'WIFE'].some(role => roles[role] !== r.parent_roles?.[role])
      )
        op.parent_roles = roles
      const children = (f.children || []).filter(Boolean)
      if (r.create || JSON.stringify(children) !== JSON.stringify(r.children))
        op.children = children
    } else {
      const given = r.given || r.name.split('/')[0].trim()
      const surname = r.surname || r.name.split('/')[1]?.trim() || ''
      if (f.given !== given || f.surname !== surname || r.create)
        op.name = f.given.trim() + ' /' + f.surname.trim() + '/'
      for (const key of ['sex', 'occupation']) {
        if ((f[key] || '') !== (r[key] || '') || r.create)
          op[key] = f[key] || ''
      }
      for (const key of ['birth', 'death', 'baptism', 'burial']) {
        const event = {}
        if ((f[key + 'Date'] || '') !== (r[key]?.date || ''))
          event.date = f[key + 'Date'] || ''
        if ((f[key + 'Place'] || '') !== (r[key]?.place || ''))
          event.place = f[key + 'Place'] || ''
        if (Object.keys(event).length) op[key] = event
      }
    }
    if (f.note?.trim()) op.append_notes = [f.note.trim()]
    if (f.source?.trim()) op.append_sources = [f.source.trim()]
    return op
  }

  async save(e) {
    e.preventDefault()
    this.busy = true
    this.message = ''
    try {
      const result = await this.post('/api/master/commit/', {
        revision: this.status.revision,
        key: crypto.randomUUID(),
        operations: [this.operation()],
      })
      this.message =
        result.projection === 'ready'
          ? this._('Change saved.')
          : this._('GEDCOM saved; Gramps update needs a retry.')
      const id = this.record.xref
      await this.refresh()
      await this.load(id)
      if (result.projection === 'ready') fireEvent(window, 'db:changed')
    } catch (err) {
      this.message = err.message
    } finally {
      this.busy = false
    }
  }

  async retry() {
    this.busy = true
    try {
      const result = await this.post('/api/master/retry/', {})
      this.message =
        result.projection === 'ready'
          ? this._('Projection updated.')
          : this._('Gramps update is still pending.')
      await this.refresh()
      if (result.projection === 'ready') fireEvent(window, 'db:changed')
    } catch (e) {
      this.message = e.message
    } finally {
      this.busy = false
    }
  }

  async publish() {
    this.busy = true
    try {
      await this.post('/api/master/publish/', {revision: this.status.revision})
      await this.refresh()
      this.message = this._(
        'Revision queued for publication. Geni and book status is shown below.'
      )
    } catch (e) {
      this.message = e.message
    } finally {
      this.busy = false
    }
  }

  async download(route, name) {
    try {
      const token = await this.appState.auth.getValidAccessToken()
      const res = await fetch(__APIHOST__ + route, {
        headers: {Authorization: 'Bearer ' + token},
      })
      if (!res.ok) throw new Error(this._('Could not download the file'))
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = name
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      this.message = e.message
    }
  }

  render() {
    if (!this.appState.permissions?.canEditMaster) return html``
    const title = this.appState.frontendConfig?.learningMode
      ? 'Редактор учебного дерева'
      : this._('Tree editor and family books')
    return html`<details>
      <summary>${title}</summary>
      <md-filled-button @click=${this.refresh} ?disabled=${this.busy}>
        ${this.appState.frontendConfig?.learningMode
          ? 'Обновить дерево после изменений другого участника'
          : this._('Refresh')}
      </md-filled-button>
      <p>
        ${this._(
          'The master format is GEDCOM 5.5.1. Changes are saved to the tree file.'
        )}
      </p>
      <div class="fields">
        <label
          >${this._('Record')}<md-outlined-select
            .value=${this.kind}
            @change=${e => {
              this.kind = e.target.value
              this.record = null
            }}
          >
            <md-select-option value="person"
              ><div slot="headline">${this._('Person')}</div></md-select-option
            >
            <md-select-option value="family"
              ><div slot="headline">${this._('Family')}</div></md-select-option
            >
          </md-outlined-select></label
        >
        <label
          >${this._('Select')}<md-outlined-select
            @change=${e => this.load(e.target.value)}
          >
            <md-select-option value=""
              ><div slot="headline">
                ${this._('Choose a record')}
              </div></md-select-option
            >
            ${(this.kind === 'person' ? this.people : this.families).map(
              p =>
                html`<md-select-option value=${p.xref}
                  ><div slot="headline">
                    ${p.name?.replaceAll('/', '') || this.familyName(p)}
                  </div></md-select-option
                >`
            )}
          </md-outlined-select></label
        >
      </div>
      <md-filled-button @click=${this.newRecord} ?disabled=${this.busy}>
        ${this._(this.kind === 'person' ? 'Add person' : 'Add family')}
      </md-filled-button>
      ${this.record
        ? html`<form @submit=${this.save}>
            <div class="fields">
              ${this.kind === 'person'
                ? html`${this.field('surname', 'Surname')}${this.field(
                      'given',
                      'Given names'
                    )}<label
                      >${this._('Sex')}<md-outlined-select
                        .value=${this.form.sex || 'U'}
                        @change=${e => {
                          this.form = {...this.form, sex: e.target.value}
                        }}
                      >
                        <md-select-option value="U"
                          ><div slot="headline">
                            ${this._('Not specified')}
                          </div></md-select-option
                        >
                        <md-select-option value="M"
                          ><div slot="headline">
                            ${this._('Male')}
                          </div></md-select-option
                        >
                        <md-select-option value="F"
                          ><div slot="headline">
                            ${this._('Female')}
                          </div></md-select-option
                        >
                      </md-outlined-select></label
                    >${this.field('occupation', 'Occupation')}${[
                      'birth',
                      'death',
                      'baptism',
                      'burial',
                    ].map(
                      (key, i) =>
                        html`${this.field(
                          key + 'Date',
                          this._(
                            'Date of %s',
                            this._(['Birth', 'Death', 'Baptism', 'Burial'][i])
                          )
                        )}${this.field(key + 'Place', 'Place')}`
                    )}`
                : html`${this.parent('HUSB', 'Father')}${this.parent(
                    'WIFE',
                    'Mother'
                  )}${this.members('children', 'Children')}`}
              <label
                >${this._('Add a note')}<md-outlined-text-field
                  type="textarea"
                  rows="3"
                  .value=${this.form.note || ''}
                  @input=${e => {
                    this.form = {...this.form, note: e.target.value}
                  }}
                ></md-outlined-text-field></label
              >${this.field('source', 'Source or document reference')}
            </div>
            <md-filled-button
              type="submit"
              ?disabled=${this.busy || this.status.projection !== 'ready'}
            >
              ${this._('Save')}
            </md-filled-button>
          </form>`
        : html``}
      ${this.status.projection === 'pending' &&
      (!this.appState.frontendConfig?.learningMode ||
        this.appState.permissions.canCurateLearning)
        ? html`<p>${this._('Gramps update is pending a retry.')}</p>
            <md-filled-button @click=${this.retry} ?disabled=${this.busy}>
              ${this._('Retry update')}
            </md-filled-button>`
        : html``}
      ${!this.appState.frontendConfig?.learningMode
        ? html`<md-filled-button
              @click=${this.publish}
              ?disabled=${this.busy || this.status.projection !== 'ready'}
            >
              ${this._('Update Geni and books')}</md-filled-button
            ><md-filled-button
              @click=${() =>
                this.download('/api/master/download/', 'export-Forest.ged')}
            >
              ${this._('Download GEDCOM')}
            </md-filled-button>`
        : html``}
      ${this.appState.frontendConfig?.learningMode &&
      this.appState.permissions.canCurateLearning
        ? html`<section aria-label="Куратор">
            <p>
              Публичный снимок не меняется при работе участников. Перед
              обновлением проверьте дерево и источники.
            </p>
            <label
              ><input
                type="checkbox"
                id="synthetic-confirm"
                .checked=${this.confirmedRevision === this.status.revision}
                @change=${e => {
                  this.confirmedRevision = e.target.checked
                    ? this.status.revision
                    : null
                }}
              />Все данные вымышлены и проверены для открытого показа</label
            >
            <md-filled-button
              @click=${this.approveDemo}
              ?disabled=${this.busy || this.status.projection !== 'ready'}
              >Обновить публичный пример</md-filled-button
            >
            <md-filled-button @click=${this.showAudit}
              >Журнал изменений</md-filled-button
            >
            <ul>
              ${this.audit.map(
                entry =>
                  html`<li>
                    ${entry.created}: ${entry.actor.subject} —
                    ${entry.before_rev} → ${entry.revision}
                  </li>`
              )}
            </ul>
          </section>`
        : html``}
      <p class="message" role="status">${this.message}</p>
      ${(this.status.publications || [])
        .slice(0, 1)
        .map(
          p =>
            html`<p>
              ${this._(
                'Books: %s. Geni: %s.',
                this.stateLabel(p.books_state),
                this.stateLabel(p.geni_state)
              )}
            </p>`
        )}
      ${this.books.map(
        b =>
          html`<md-filled-button
            @click=${() =>
              this.download(
                '/api/master/books/' + encodeURIComponent(b.file),
                b.file
              )}
          >
            ${b.title || b.file}
          </md-filled-button>`
      )}
    </details>`
  }
}
customElements.define('grampsjs-master-editor', GrampsjsMasterEditor)
