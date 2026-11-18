const main = document.querySelector('main')
const date = value => value && Number.isFinite(new Date(value).getTime())
  ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  : 'Not yet'
const outcomes = {
  report_only: 'Poll complete', quorum_not_met: 'Quorum not met', passed: 'Passed', rejected: 'Rejected',
}
const reportPath = /^\/ballots\/[0-9a-f-]{36}$/

function el(tag, props = {}, ...children) {
  const element = document.createElement(tag)
  for (const [key, value] of Object.entries(props)) element.setAttribute(key, value)
  element.append(...children.filter(child => child != null))
  return element
}
const p = (text, cls = 'muted') => el('p', { class: cls }, text)
const code = value => el('code', {}, value)
const link = (url, text, cls = '') => el('a', { href: url, class: cls }, text)
const pill = (text, tone = '') => el('span', { class: 'pill ' + tone }, text)
const panel = (...children) => el('section', { class: 'panel stack' }, ...children)
const row = (...children) => el('div', { class: 'row' }, ...children)
const hero = (label, title, description) => el('section', { class: 'hero stack' },
  p(label, 'eyebrow'), el('h1', {}, title), p(description))
const details = pairs => el('dl', { class: 'data' }, ...pairs.map(([label, value]) =>
  el('div', {}, el('dt', {}, label), el('dd', {}, value))))
const feedback = (text, error = true) => el('p', { class: error ? 'error' : 'muted', role: error ? 'alert' : 'status' }, text)
const limitation = 'Proof checks cover the votes supplied with the ballot and their tally. They do not show that the roster was authorized or that every submitted vote was included.'

function button(text, action, primary = false) {
  const result = el('button', { type: 'button', class: primary ? 'primary' : '' }, text)
  result.addEventListener('click', action)
  return result
}

async function api(path, payload) {
  const options = { headers: { Accept: 'application/json' } }
  if (payload !== undefined) {
    options.method = 'POST'
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(payload)
  }
  const response = await fetch(path, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || 'Could not reach this verifier. Try again in a moment.')
  return data
}

function theme() {
  const toggle = document.querySelector('#theme')
  const scheme = matchMedia('(prefers-color-scheme: dark)')
  let preference = 'system'
  try { preference = localStorage.getItem('cope-verifier-theme') || 'system' } catch {}
  const apply = () => {
    document.documentElement.dataset.theme = preference === 'system' ? scheme.matches ? 'dark' : 'light' : preference
    toggle.textContent = preference === 'system' ? 'System theme' : preference === 'dark' ? 'Dark' : 'Light'
  }
  toggle.addEventListener('click', () => {
    preference = preference === 'system' ? 'light' : preference === 'light' ? 'dark' : 'system'
    try { localStorage.setItem('cope-verifier-theme', preference) } catch {}
    apply()
  })
  scheme.addEventListener('change', apply)
  apply()
}

async function archive() {
  const status = await api('/api/status')
  const ballots = status.ballots.filter(path => reportPath.test(path))
  const fresh = status.last_scan && Date.now() - Date.parse(status.last_scan) < 5 * 60 * 1000
  const count = ballots.length
  const list = el('ul', { class: 'list' })
  const navigation = row()
  const listStatus = el('p', { class: 'muted', role: 'status' })
  const pageSize = 12
  async function showPage(page) {
    const start = page * pageSize
    navigation.querySelectorAll('button').forEach(item => { item.disabled = true })
    listStatus.textContent = 'Loading reports…'
    const reports = await Promise.allSettled(ballots.slice(start, start + pageSize).map(path => api(path)))
    list.replaceChildren(...reports.map((result, index) => {
      const report = result.status === 'fulfilled' ? result.value : null
      const path = ballots[start + index]
      return el('li', { class: 'stack compact' },
        row(link(path, report?.title || path.split('/').at(-1)),
          report?.conflict ? pill('Conflicting evidence', 'warning') : report ? pill('Proofs checked', 'success') : null),
        p(report ? (outcomes[report.result?.outcome] || 'Result') + ' · Observed ' + date(report.observed_at) : 'Report details are unavailable.'))
    }))
    listStatus.textContent = 'Showing ' + (start + 1) + '–' + (start + reports.length) + ' of ' + count + ' reports'
    const previous = button('Previous', () => void showPage(page - 1))
    const next = button('Next', () => void showPage(page + 1))
    previous.disabled = page === 0
    next.disabled = start + pageSize >= count
    navigation.replaceChildren(previous, next)
  }
  main.replaceChildren(
    hero('Verification archive', 'Quorum Verifier', 'Browse completed COPE ballots checked and archived by this independent witness.'),
    panel(el('div', { class: 'row between' }, el('h2', {}, 'Verifier status'),
      pill(!fresh ? 'Waiting for a scan' : status.scan_errors ? 'Scan completed with errors' : 'Scanning normally',
        fresh && !status.scan_errors ? 'success' : 'warning')),
    details([
      ['Follows', link(status.origin + '/quorum', status.origin)],
      ['Last successful directory scan', date(status.last_scan)],
      ['Archived ballots', String(count)],
      ['Witness public key', code(status.public_key)],
    ])),
    panel(el('h2', {}, 'Ballot reports'),
      count ? listStatus : p('No completed ballots have been archived yet.'), list, navigation, p(limitation)),
  )
  if (count) await showPage(0)
}

async function report(path) {
  const value = await api(path)
  const result = value.result
  const checks = (await api('/api/status')).witness_policy ? value.witness_checks : null
  document.title = value.title + ' · Quorum Verifier'
  const tally = el('ul', { class: 'list tally' }, ...result.options.map((option, i) =>
    el('li', {}, el('div', { class: 'row between' }, el('span', {}, option), el('strong', {}, String(result.counts[i]))))))
  main.replaceChildren(
    link('/', '← Verification archive'),
    hero('Ballot report', value.title, 'First observed ' + date(value.observed_at)),
    value.conflict ? panel(row(pill('Conflicting evidence', 'warning')), p('The verifier later saw different valid evidence for this ballot. The first observation is preserved.'),
      details([['Conflicting digest', code(value.conflict.digest)], ['Observed', date(value.conflict.observed_at)]])) : document.createComment(''),
    panel(row(el('h2', {}, 'Recorded tally'), pill(outcomes[result.outcome] || 'Completed')),
      p(result.total + ' votes supplied for ' + result.electorate + ' roster entries.'), tally, p(limitation)),
    panel(el('h2', {}, 'Evidence and signature'),
      details([
        ['Evidence SHA-256', code(value.digest)],
        ['Witness signature', value.signature ? code(value.signature) : 'No signature from this verifier'],
        ...(value.public_key ? [['Witness public key', code(value.public_key)]] : []),
        ...(value.signature_published_at ? [['Published to COPE', date(value.signature_published_at)]] : []),
      ]),
      p(checks ? checks.policy_matches
        ? checks.checked.length + ' of ' + checks.operators + ' pinned witness signatures checked (' + checks.threshold + ' required).'
        : 'The ballot’s witness list differs from this verifier’s pinned policy.'
        : 'There is no pinned witness signature check for this report.'),
      checks?.checked.length ? details([['Operators whose signatures checked out', checks.checked.join(', ')]]) : null,
      row(link(path + '/evidence', 'Ballot evidence', 'button'), link(path + '?format=json', 'Report JSON', 'button'))),
  )
}

const stateText = {
  disabled: 'No release policy has been configured by this verifier’s operator.',
  checking: 'Checking for a signed release…',
  current: 'This verifier is running the published release.',
  installed: 'The approved release was installed.',
  applying: 'Installing the approved release. The verifier will restart.',
}

async function admin() {
  document.title = 'Admin · Quorum Verifier'
  const heading = hero('Verifier administration', 'Admin', 'Review signed software releases and choose when this verifier installs them.')
  let signedIn = await api('/api/admin/session')
  let timer
  let waitUntil

  function login(message) {
    clearTimeout(timer)
    if (!signedIn.enabled) {
      main.replaceChildren(heading, panel(el('h2', {}, 'Admin access unavailable'),
        p('Use the verifier’s configured admin address. If this verifier is yours, configure its local admin token.')))
      return
    }
    const input = el('input', { id: 'admin-token', type: 'password', autocomplete: 'current-password', maxlength: '128', required: '' })
    const submit = el('button', { type: 'submit', class: 'primary' }, 'Sign in')
    const messages = el('div')
    if (message) messages.append(feedback(message, false))
    const form = el('form', { class: 'stack' }, el('label', { for: 'admin-token' }, 'Admin token'), input,
      messages, row(submit))
    form.addEventListener('submit', async event => {
      event.preventDefault()
      submit.disabled = true
      try {
        await api('/api/admin/session', { token: input.value })
        input.value = ''
        await dashboard()
      } catch (error) {
        messages.replaceChildren(feedback(error.message))
        input.focus()
      } finally {
        submit.disabled = false
      }
    })
    const card = panel(el('h2', {}, 'Admin sign in'), p('Enter the token configured on this verifier.'), form)
    card.classList.add('narrow')
    main.replaceChildren(heading, card)
  }

  async function dashboard() {
    clearTimeout(timer)
    signedIn = await api('/api/admin/session')
    if (!signedIn.authenticated) {
      login()
      return
    }
    const status = await api('/api/admin/update')
    const offer = status.state === 'available' ? status.offer : null
    const controls = row(button('Refresh', () => void dashboard().catch(showError)), button('Sign out', async () => {
      try {
        await api('/api/admin/logout', {})
        login()
      } catch (error) { showError(error) }
    }))
    const cards = [panel(el('div', { class: 'row between' }, el('h2', {}, 'Software updates'), controls),
      details([['Running revision', code(status.commit || 'Local build')],
        ['Release sequence', status.sequence == null ? 'Bootstrap' : String(status.sequence)]]),
      status.message || stateText[status.state] ? feedback(status.message || stateText[status.state], Boolean(status.message)) : null,
      p('Releases are checked in the background. Installation requires an explicit approval on this verifier.'))]
    if (offer) {
      const messages = el('div')
      const approve = button('Review and install…', () => {
        approve.disabled = true
        const cancel = button('Cancel', () => { messages.replaceChildren(); approve.disabled = false })
        const confirm = button('Approve and restart', async () => {
          confirm.disabled = cancel.disabled = true
          try {
            await api('/api/admin/update/approve', { digest: offer.digest, challenge: offer.challenge })
            messages.replaceChildren(feedback('Approval recorded. The verifier will restart if the release checks pass.', false))
            waitUntil = Date.now() + 90000
            timer = setTimeout(() => void waitForRestart(), 2000)
          } catch (error) {
            messages.replaceChildren(feedback(error.message))
            approve.disabled = false
          }
        }, true)
        messages.replaceChildren(feedback('Install release ' + offer.sequence + ' on this verifier?', false), row(confirm, cancel))
        confirm.focus()
      }, true)
      cards.push(panel(row(el('h2', {}, 'Release ready for review'), pill('Signatures checked', 'success')),
        details([
          ['Revision', code(offer.commit)], ['Channel and sequence', offer.channel + ' · ' + offer.sequence],
          ['Release signers', offer.signers.join(', ') + ' (' + status.threshold + ' required)'],
          ['Expires', date(offer.expires_at * 1000)], ['Artifact SHA-256', code(offer.artifact.sha256)],
          ['Manifest SHA-256', code(offer.digest)],
        ]), row(approve), messages))
    }
    main.replaceChildren(heading, ...cards)
  }

  function showError(error) {
    main.append(feedback(error.message))
  }

  async function waitForRestart() {
    if (Date.now() >= waitUntil) {
      return dashboard().catch(showError)
    }
    try {
      signedIn = await api('/api/admin/session')
      if (!signedIn.authenticated) return login('The verifier restarted. Sign in to see the active release.')
      const update = await api('/api/admin/update')
      if (update.state === 'applying' || update.state === 'available') {
        timer = setTimeout(() => void waitForRestart(), 2000)
      } else await dashboard()
    } catch {
      timer = setTimeout(() => void waitForRestart(), 2000)
    }
  }

  if (signedIn.authenticated) await dashboard()
  else login()
}

async function run() {
  theme()
  const path = location.pathname
  document.querySelector(path === '/admin' ? '#admin-nav' : '#archive-nav').setAttribute('aria-current', 'page')
  try {
    if (path === '/admin') await admin()
    else if (reportPath.test(path)) await report(path)
    else await archive()
  } catch (error) {
    main.replaceChildren(hero('Quorum Verifier', 'This page could not load', error.message), link('/', 'Back to the archive'))
  } finally {
    main.setAttribute('aria-busy', 'false')
  }
}

void run()
