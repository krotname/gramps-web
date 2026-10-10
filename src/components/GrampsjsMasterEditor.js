import {LitElement, css, html} from 'lit'
import {GrampsjsAppStateMixin} from '../mixins/GrampsjsAppStateMixin.js'
import {apiPutPostDelete, __APIHOST__} from '../api.js'
import {fireEvent} from '../util.js'

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
      input,
      select,
      textarea {
        font: inherit;
        padding: 10px;
        border: 1px solid #999;
        border-radius: 6px;
        background: var(--md-sys-color-surface, #fff);
        color: inherit;
      }
      textarea {
        min-height: 80px;
      }
      button {
        padding: 10px 16px;
        cursor: pointer;
        border: 1px solid #888;
        border-radius: 8px;
        margin: 8px 8px 8px 0;
        font: inherit;
      }
      button:disabled {
        cursor: wait;
        opacity: 0.6;
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
  }

  updated(changed) {
    if (changed.has('appState') && !this.loaded && this.appState.auth) {
      this.loaded = true
      this.refresh()
    }
  }

  async get(route) {
    const r = await this.appState.apiGet(route)
    if (r.error) throw new Error(r.error)
    return r.data
  }

  async post(route, payload) {
    const r = await apiPutPostDelete(
      this.appState.auth,
      'POST',
      route,
      payload,
      {dbChanged: false}
    )
    if (r.error) throw new Error(r.error)
    return r.data
  }

  async refresh() {
    try {
      this.status = await this.get('/api/master/status/')
      this.people = await this.get('/api/master/people/')
      this.families = await this.get('/api/master/families/')
      const b = await this.get('/api/master/books/')
      this.books = b.books || []
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
          parents: [...this.record.parents],
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
    return html`<label
      >${label}<input
        .value=${this.form[key] || ''}
        @input=${e => {
          this.form = {...this.form, [key]: e.target.value}
        }}
    /></label>`
  }

  familyName(family) {
    return (
      family.parents
        .map(
          id =>
            this.people.find(p => p.xref === id)?.name.replaceAll('/', '') || id
        )
        .join(' и ') || 'Семья без указанных родителей'
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
      <p>${label}</p>
      ${ids.map(
        (id, index) =>
          html`<label
            >${label === 'Дети' ? 'Ребёнок' : 'Родитель'} ${index + 1}<select
              .value=${id}
              @change=${e => change(index, e.target.value)}
            >
              <option value="">Убрать из семьи</option>
              ${this.people.map(
                p =>
                  html`<option value=${p.xref}>
                    ${p.name.replaceAll('/', '')}
                  </option>`
              )}
            </select></label
          >`
      )}${ids.length < limit
        ? html`<label
            >Добавить<select
              .value=${''}
              @change=${e => change(ids.length, e.target.value)}
            >
              <option value="">Выберите человека</option>
              ${this.people
                .filter(p => !ids.includes(p.xref))
                .map(
                  p =>
                    html`<option value=${p.xref}>
                      ${p.name.replaceAll('/', '')}
                    </option>`
                )}
            </select></label
          >`
        : ''}
    </div>`
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
      parents: [],
      children: [],
    }
    this.form = {
      given: '',
      surname: '',
      sex: 'U',
      parents: [],
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
      for (const key of ['parents', 'children']) {
        const values = (f[key] || []).filter(Boolean)
        if (JSON.stringify(values) !== JSON.stringify(r[key]) || r.create)
          op[key] = values
      }
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
          ? 'Изменение сохранено.'
          : 'GEDCOM сохранён; обновление Gramps требует повтора.'
      const id = this.record.xref
      await this.refresh()
      await this.load(id)
      fireEvent(window, 'db:changed')
    } catch (err) {
      this.message = err.message
    } finally {
      this.busy = false
    }
  }

  async retry() {
    this.busy = true
    try {
      await this.post('/api/master/retry/', {})
      await this.refresh()
      fireEvent(window, 'db:changed')
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
      this.message =
        'Редакция поставлена в очередь публикации. Готовность Geni и книг показана ниже.'
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
      if (!res.ok) throw new Error('Не удалось скачать файл')
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
    return html`<details>
      <summary>Редактор дерева и семейные книги</summary>
      <p>
        Основной формат — GEDCOM 5.5.1. Изменения сохраняются в файл дерева.
      </p>
      <div class="fields">
        <label
          >Запись<select
            .value=${this.kind}
            @change=${e => {
              this.kind = e.target.value
              this.record = null
            }}
          >
            <option value="person">Человек</option>
            <option value="family">Семья</option>
          </select></label
        >
        <label
          >Выбрать<select @change=${e => this.load(e.target.value)}>
            <option value="">Выберите запись</option>
            ${(this.kind === 'person' ? this.people : this.families).map(
              p =>
                html`<option value=${p.xref}>
                  ${p.name?.replaceAll('/', '') || this.familyName(p)}
                </option>`
            )}
          </select></label
        >
      </div>
      <button @click=${this.newRecord} ?disabled=${this.busy}>
        Добавить ${this.kind === 'person' ? 'человека' : 'семью'}
      </button>
      ${this.record
        ? html`<form @submit=${this.save}>
            <div class="fields">
              ${this.kind === 'person'
                ? html`${this.field('surname', 'Фамилия')}${this.field(
                      'given',
                      'Имя и отчество'
                    )}<label
                      >Пол<select
                        .value=${this.form.sex || 'U'}
                        @change=${e => {
                          this.form = {...this.form, sex: e.target.value}
                        }}
                      >
                        <option value="U">Не указан</option>
                        <option value="M">Мужской</option>
                        <option value="F">Женский</option>
                      </select></label
                    >${this.field('occupation', 'Занятие')}${[
                      'birth',
                      'death',
                      'baptism',
                      'burial',
                    ].map(
                      (key, i) =>
                        html`${this.field(
                          key + 'Date',
                          ['Рождение', 'Смерть', 'Крещение', 'Погребение'][i] +
                            ' — дата'
                        )}${this.field(key + 'Place', 'Место')}`
                    )}`
                : html`${this.members('parents', 'Родители', 2)}${this.members(
                    'children',
                    'Дети'
                  )}`}
              <label
                >Добавить заметку<textarea
                  .value=${this.form.note || ''}
                  @input=${e => {
                    this.form = {...this.form, note: e.target.value}
                  }}
                ></textarea></label
              >${this.field('source', 'Источник или реквизиты документа')}
            </div>
            <button
              type="submit"
              ?disabled=${this.busy || this.status.projection !== 'ready'}
            >
              Сохранить
            </button>
          </form>`
        : html``}
      ${this.status.projection === 'pending'
        ? html`<p>Обновление Gramps ожидает повтора.</p>
            <button @click=${this.retry} ?disabled=${this.busy}>
              Повторить обновление
            </button>`
        : html``}
      <button
        @click=${this.publish}
        ?disabled=${this.busy || this.status.projection !== 'ready'}
      >
        Обновить Geni и книги</button
      ><button
        @click=${() =>
          this.download('/api/master/download/', 'export-Forest.ged')}
      >
        Скачать GEDCOM
      </button>
      <p class="message" role="status">${this.message}</p>
      ${(this.status.publications || [])
        .slice(0, 1)
        .map(p => html`<p>Книги: ${p.books_state}. Geni: ${p.geni_state}.</p>`)}
      ${this.books.map(
        b =>
          html`<button
            @click=${() =>
              this.download(
                '/api/master/books/' + encodeURIComponent(b.file),
                b.file
              )}
          >
            ${b.title || b.file}
          </button>`
      )}
    </details>`
  }
}
customElements.define('grampsjs-master-editor', GrampsjsMasterEditor)
