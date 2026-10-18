function paragraph(parent, text) {
  const node = document.createElement('p')
  node.textContent = text
  parent.append(node)
}

function card(parent, id, title, notes) {
  const article = document.createElement('article')
  article.id = id
  const heading = document.createElement('h2')
  heading.textContent = title
  article.append(heading)
  notes.forEach(note => paragraph(article, note))
  parent.append(article)
  return article
}

async function load() {
  const state = document.querySelector('#state')
  try {
    const response = await fetch('/api/learning/public/', {credentials: 'omit'})
    if (!response.ok) throw new Error('unavailable')
    const snapshot = await response.json()
    for (const person of snapshot.people) {
      const article = card(
        document.querySelector('#people'),
        'person-' + person.id,
        person.name,
        person.notes
      )
      for (const id of person.sources) {
        const link = document.createElement('a')
        link.href = '#source-' + encodeURIComponent(id)
        link.textContent = 'Источник ' + id + ' '
        article.append(link)
      }
    }
    for (const source of snapshot.sources) {
      card(
        document.querySelector('#sources'),
        'source-' + source.id,
        source.id + ' · ' + source.title,
        source.notes
      )
    }
    state.textContent =
      'Опубликован куратором: ' +
      snapshot.published_at +
      ' · версия дерева ' +
      snapshot.revision.slice(0, 12)
    if (location.hash)
      document
        .getElementById(decodeURIComponent(location.hash.slice(1)))
        ?.scrollIntoView()
  } catch {
    state.textContent =
      'Опубликованный пример сейчас недоступен. Открытый урок и задание доступны по ссылкам на этой странице.'
  }
}

load()
