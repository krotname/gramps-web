// Index of the people returned by the people endpoint with
// `extend=primary_parent_family,family_list`, and optionally
// `parent_family_list`, and of the families they refer to.
export class FamilyGraph {
  constructor(people = []) {
    this._people = new Map()
    this._peopleByGrampsId = new Map()
    this._families = new Map()
    for (const person of people) {
      this._people.set(person.handle, person)
      if (!this._peopleByGrampsId.has(person.gramps_id)) {
        this._peopleByGrampsId.set(person.gramps_id, person)
      }
      const {
        families = [],
        primary_parent_family: primaryParentFamily,
        parent_families: parentFamilies = [],
      } = person.extended ?? {}
      for (const family of [
        ...families,
        primaryParentFamily,
        ...parentFamilies,
      ]) {
        if (family?.handle && !this._families.has(family.handle)) {
          this._families.set(family.handle, family)
        }
      }
    }
  }

  // Returns all people, in the order they were fetched
  people() {
    return [...this._people.values()]
  }

  // Selects relatives by parent, child, sibling or partner steps. Only fetched people
  // participate, so this also respects the API's visibility restrictions.
  withinSeparation(grampsId, separation) {
    const root = this.personByGrampsId(grampsId)
    if (!root) return []
    const neighbours = new Map(
      this.people().map(person => [person.handle, new Set()])
    )
    const connect = (left, right) => {
      if (!neighbours.has(left) || !neighbours.has(right)) return
      neighbours.get(left).add(right)
      neighbours.get(right).add(left)
    }
    const connectSiblings = children => {
      for (const left of children) {
        for (const right of children) connect(left, right)
      }
    }
    const childrenByParent = new Map()
    for (const family of this._families.values()) {
      const {father_handle: father, mother_handle: mother} = family
      connect(father, mother)
      const children = (family.child_ref_list ?? []).map(child => child.ref)
      connectSiblings(children)
      for (const parent of [father, mother].filter(Boolean)) {
        if (!childrenByParent.has(parent))
          childrenByParent.set(parent, new Set())
        for (const child of children) childrenByParent.get(parent).add(child)
      }
      for (const child of family.child_ref_list ?? []) {
        connect(father, child.ref)
        connect(mother, child.ref)
      }
    }
    for (const children of childrenByParent.values()) connectSiblings(children)
    const selected = new Set([root.handle])
    let frontier = [root.handle]
    for (let depth = 0; depth < separation && frontier.length; depth += 1) {
      const next = []
      for (const handle of frontier) {
        for (const neighbour of neighbours.get(handle)) {
          if (!selected.has(neighbour)) {
            selected.add(neighbour)
            next.push(neighbour)
          }
        }
      }
      frontier = next
    }
    return this.people().filter(person => selected.has(person.handle))
  }

  // Returns the person with the given handle, or undefined if not fetched
  person(handle) {
    return this._people.get(handle)
  }

  // Returns the person with the given Gramps ID, or undefined if not fetched
  personByGrampsId(grampsId) {
    return this._peopleByGrampsId.get(grampsId)
  }

  // Returns the family with the given handle, or undefined if no fetched
  // person refers to it
  family(handle) {
    return this._families.get(handle)
  }

  // Returns the families in the person's family list, in which they are a
  // partner
  partnerFamilies(handle) {
    return this.person(handle)?.extended?.families ?? []
  }

  // Returns the person's parent families: the primary one first, then the
  // other ones if they were fetched with `extend=parent_family_list`
  parentFamilies(handle) {
    const extended = this.person(handle)?.extended ?? {}
    const seen = new Set()
    return [
      extended.primary_parent_family,
      ...(extended.parent_families ?? []),
    ].filter(family => {
      if (!family?.handle || seen.has(family.handle)) {
        return false
      }
      seen.add(family.handle)
      return true
    })
  }

  // Returns the father and mother handles of the person's primary parent
  // family, with an empty string for an unknown parent
  parents(handle) {
    const family = this.person(handle)?.extended?.primary_parent_family
    return {
      father: family?.father_handle || '',
      mother: family?.mother_handle || '',
    }
  }

  // Returns the handles of the person's children in all families where they
  // are a parent. With `birthOnly`, only children whose relation to this
  // parent is Birth are included.
  children(handle, {birthOnly = false} = {}) {
    return this.partnerFamilies(handle).flatMap(family => {
      const isFather = family.father_handle === handle
      if (!isFather && family.mother_handle !== handle) {
        return []
      }
      const relationKey = isFather ? 'frel' : 'mrel'
      return (family.child_ref_list || [])
        .filter(childRef => !birthOnly || childRef[relationKey] === 'Birth')
        .map(childRef => childRef.ref)
    })
  }
}
