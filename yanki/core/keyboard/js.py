"""Klavye modülünün sayfaya enjekte ettiği JavaScript (KLV-01..10).

Öğe kimlikleri ``window.__yanki`` altındaki bir ``WeakMap`` ile tutulur;
DOM'a öznitelik yazılmaz. Kod sayfanın ana dünyasında çalışır (Faz 0'daki axe
ile aynı sınır): sayfa kodu bu nesneyi görebilir ya da bozabilir.

Odak çözümü ``document.activeElement``'ten başlar; shadow root'lar ve aynı
kökenli iframe'ler boyunca iner. Başka kökenli bir iframe'in içine
girilemez: odak orada olduğu sürece ``opaque`` döner.
"""

from __future__ import annotations

INSTALL = r"""
() => {
  if (window.__yanki && window.__yanki.v === 1) return;
  const ids = new WeakMap();
  const refs = new Map();
  let next = 1;
  const idOf = (el) => {
    let id = ids.get(el);
    if (!id) { id = next++; ids.set(el, id); refs.set(id, new WeakRef(el)); }
    return id;
  };
  const get = (id) => { const r = refs.get(id); return r ? (r.deref() || null) : null; };
  const visibleEl = (el) => {
    if (!el.isConnected) return false;
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    }
    return el.getClientRects().length > 0;
  };
  const TABBABLE = 'a[href], area[href], button, input:not([type="hidden" i]), select, ' +
    'textarea, iframe, summary, [tabindex], audio[controls], video[controls], ' +
    '[contenteditable]:not([contenteditable="false" i])';
  const isTabbable = (el) => {
    if (!el.matches(TABBABLE) || el.tabIndex < 0 || el.disabled) return false;
    if (el.closest('[inert]') || !visibleEl(el)) return false;
    const closed = el.closest('details:not([open])');
    if (closed && !(el.localName === 'summary' && el.parentElement === closed)) return false;
    return true;
  };
  const DIALOG = 'dialog[open], [role="dialog"], [role="alertdialog"], [aria-modal="true"]';
  const modalOf = (el) => {
    const m = el.closest ? el.closest('dialog[open], [aria-modal="true"]') : null;
    return m ? idOf(m) : null;
  };
  // Odaklı en derin öğe: shadow root ve aynı kökenli iframe'ler boyunca iner.
  const deepActive = () => {
    if (!document.hasFocus()) return null;
    let el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    let ox = 0, oy = 0, opaque = false;
    for (let guard = 0; guard < 32; guard++) {
      if (el.shadowRoot && el.shadowRoot.activeElement) {
        el = el.shadowRoot.activeElement;
        continue;
      }
      if (el.localName === 'iframe' || el.localName === 'frame') {
        let doc = null;
        try { doc = el.contentDocument; } catch (e) { doc = null; }
        const inner = doc ? doc.activeElement : null;
        if (inner && inner !== doc.body && inner !== doc.documentElement) {
          const r = el.getBoundingClientRect();
          ox += r.left + el.clientLeft; oy += r.top + el.clientTop;
          el = inner;
          continue;
        }
        opaque = true;
      }
      break;
    }
    return { el, ox, oy, opaque };
  };
  // Bir öğe başka bir öğenin içinde mi (shadow host'lar boyunca).
  const within = (container, el) => {
    let n = el;
    while (n) {
      if (n === container || container.contains(n)) return true;
      const root = n.getRootNode();
      n = (root instanceof ShadowRoot) ? root.host
        : (root.defaultView && root !== document ? root.defaultView.frameElement : null);
    }
    return false;
  };
  const fixedVisible = () => {
    const out = new Set();
    for (const el of document.body ? document.body.querySelectorAll('*') : []) {
      if (getComputedStyle(el).position === 'fixed' && visibleEl(el)) out.add(idOf(el));
    }
    return out;
  };
  const area = (r) => Math.max(0, r.width) * Math.max(0, r.height);
  const coverRatio = (el) => {
    const r = el.getBoundingClientRect();
    const w = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
    return (w > 0 && h > 0) ? (w * h) / (innerWidth * innerHeight) : 0;
  };
  // Görünür dialoglar. Anlamsal olanlar güvenilir; rolü olmayan sabit
  // katmanlar yalnızca izleme başladığında görünür değillerse sayılır.
  const dialogs = (minCover, fixedBefore) => {
    const out = [];
    for (const el of document.querySelectorAll(DIALOG)) {
      if (!visibleEl(el) || area(el.getBoundingClientRect()) <= 0) continue;
      if (out.some(d => get(d.id) && get(d.id).contains(el))) continue;
      out.push({ id: idOf(el), heuristic: false });
    }
    if (out.length || !fixedBefore) return out;
    const fresh = [];
    let covered = false;
    for (const el of document.body ? document.body.querySelectorAll('*') : []) {
      if (getComputedStyle(el).position !== 'fixed' || !visibleEl(el)) continue;
      const id = idOf(el);
      if (fixedBefore.has(id)) continue;
      if (coverRatio(el) >= minCover) covered = true;
      if ([...el.querySelectorAll('*')].some(isTabbable)) fresh.push(el);
    }
    if (!covered) return out;
    for (const el of fresh) {
      if (fresh.some(o => o !== el && o.contains(el))) continue;
      out.push({ id: idOf(el), heuristic: true });
    }
    return out;
  };
  window.__yanki = {
    v: 1, idOf, get, visibleEl, isTabbable, modalOf, deepActive, within, dialogs,
    fixedVisible, mut: 0, obs: null, watch: null,
  };
}
"""

SETTLE = """
() => new Promise((resolve) => {
  let done = false;
  const finish = () => { if (!done) { done = true; resolve(); } };
  requestAnimationFrame(() => requestAnimationFrame(finish));
  setTimeout(finish, 100);
})
"""

PAGE_INFO = """
() => ({
  rtl: getComputedStyle(document.documentElement).direction === 'rtl',
  vw: innerWidth,
  vh: innerHeight,
})
"""

READ_FOCUS = """
() => {
  const Y = window.__yanki;
  const scroll = [scrollX, scrollY];
  const a = Y.deepActive();
  if (!a) return { id: null, opaque: false, tag: null, rect: null, scroll, modal: null,
                   visible: false, reason: null };
  const el = a.el;
  const r = el.getBoundingClientRect();
  const rect = { x: r.left + a.ox, y: r.top + a.oy, width: r.width, height: r.height };
  let reason = null;
  if (!Y.visibleEl(el)) reason = 'hidden';
  else if (r.width * r.height < 4) reason = 'too_small';
  else if (rect.x + rect.width <= 0 || rect.y + rect.height <= 0 ||
           rect.x >= innerWidth || rect.y >= innerHeight) reason = 'offscreen';
  return { id: Y.idOf(el), opaque: a.opaque, tag: el.localName, rect, scroll,
           modal: Y.modalOf(el), visible: reason === null, reason };
}
"""

BLUR = """
() => {
  const Y = window.__yanki;
  const a = Y.deepActive();
  if (a && a.el.blur) a.el.blur();
  if (document.activeElement && document.activeElement !== document.body) {
    document.activeElement.blur();
  }
}
"""

SCROLL_TO = "([x, y]) => window.scrollTo(x, y)"

# Seçici, rol ve erişilebilir ad: collect.py ile aynı axe yardımcıları.
DESCRIBE = r"""
(ids) => {
  const Y = window.__yanki;
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const cssPath = (el) => {
    const parts = [];
    while (el && el.nodeType === 1) {
      const root = el.getRootNode();
      if (el.id && root.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
        parts.unshift('#' + CSS.escape(el.id));
        break;
      }
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) if (s.localName === el.localName) i++;
      parts.unshift(el.localName + ':nth-of-type(' + i + ')');
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const hasAxe = typeof axe !== 'undefined';
  let setup = false;
  if (hasAxe) { try { axe.setup(document); setup = true; } catch (e) { setup = false; } }
  try {
    const selectorOf = (el) => {
      const root = el.getRootNode();
      if (root === document) {
        if (setup) { try { return axe.utils.getSelector(el); } catch (e) { /* aşağıda */ } }
        return cssPath(el);
      }
      if (root instanceof ShadowRoot) return selectorOf(root.host) + ' >>> ' + cssPath(el);
      const frame = root.defaultView ? root.defaultView.frameElement : null;
      return (frame ? selectorOf(frame) : 'iframe') + ' >>> ' + cssPath(el);
    };
    const out = {};
    for (const id of ids) {
      const el = Y.get(id);
      if (!el) continue;
      let name = '';
      let role = el.getAttribute('role');
      if (setup && el.getRootNode() === document) {
        try { name = norm(axe.commons.text.accessibleText(el)); } catch (e) { name = ''; }
        try { role = axe.commons.aria.getRole(el) || role; } catch (e) { /* özniteliği kullan */ }
      }
      if (!name) name = norm(el.getAttribute('aria-label') || el.innerText || el.value || '');
      if (name.length > 120) name = name.slice(0, 119) + '…';
      out[id] = { selector: selectorOf(el), tag: el.localName, role: role || null, name };
    }
    return out;
  } finally {
    if (setup) axe.teardown();
  }
}
"""

# Seçiciden öğe kimliği. `` >>> `` shadow root ya da iframe sınırıdır.
RESOLVE = r"""
(selector) => {
  const Y = window.__yanki;
  let root = document;
  let el = null;
  const parts = selector.split(' >>> ');
  for (let i = 0; i < parts.length; i++) {
    try { el = root.querySelector(parts[i]); } catch (e) { return null; }
    if (!el) return null;
    if (i < parts.length - 1) {
      let next = el.shadowRoot;
      if (!next) { try { next = el.contentDocument; } catch (e) { next = null; } }
      if (!next) return null;
      root = next;
    }
  }
  return Y.idOf(el);
}
"""

FOCUS_ELEMENT = """
(id) => {
  const Y = window.__yanki;
  const el = Y.get(id);
  if (!el || typeof el.focus !== 'function') return false;
  el.focus();
  const a = Y.deepActive();
  return !!a && a.el === el;
}
"""

# Sekme durağı tahmini (ana belge). Tuzak sarmasında ziyaret edilmeyen
# durakları bulmak için kullanılır (KLV-05).
UNVISITED_AFTER = """
([cycle, visited]) => {
  const Y = window.__yanki;
  const all = [...document.querySelectorAll('*')].filter(Y.isTabbable);
  const cyc = new Set(cycle), seen = new Set(visited);
  let last = -1;
  all.forEach((el, i) => { if (cyc.has(Y.idOf(el))) last = i; });
  if (last < 0) return [];
  return all.slice(last + 1).map(Y.idOf).filter(id => !seen.has(id));
}
"""

# Atla bağlantısı adayı mı (KLV-09)?
SKIP_INFO = r"""
(id) => {
  const Y = window.__yanki;
  const el = Y.get(id);
  if (!el || el.localName !== 'a' || !el.hasAttribute('href')) return null;
  const href = el.getAttribute('href').trim();
  if (!href.startsWith('#') || href.length < 2) return null;
  let frag = href.slice(1);
  try { frag = decodeURIComponent(frag); } catch (e) { /* ham */ }
  const target = document.getElementById(frag) || document.getElementsByName(frag)[0] || null;
  const text = (el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '');
  // Tek başına "geç" kullanılmaz: "Geçmiş siparişler" gibi metinlerle eşleşir.
  const byText = /\batla\b|içeriğe (geç|git|atla)|ana içeri|\bskip\b|main content|jump to/i
    .test(text);
  const main = document.querySelector('main, [role="main"]');
  const byTarget = !!target && !!main &&
    (target === main || target.contains(main) || main.contains(target));
  if (!byText && !byTarget) return null;
  return { target: target ? Y.idOf(target) : null };
}
"""

# Atla bağlantısından sonra odak hedefe ulaştı mı (KLV-09)?
SKIP_LANDED = """
(target) => {
  const Y = window.__yanki;
  const t = Y.get(target);
  const a = Y.deepActive();
  if (!t || !a) return false;
  if (Y.within(t, a.el)) return true;
  // Hedef boş bir çapa olabilir: ondan sonraki ilk sekme durağı da kabul edilir.
  const all = [...document.querySelectorAll('*')].filter(Y.isTabbable);
  const first = all.find(el => t === el || t.contains(el) ||
    (t.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING));
  return first === a.el;
}
"""

# ``main`` öncesindeki sekme durakları (KLV-09). ``null``: ana bölge yok.
STOPS_BEFORE_MAIN = """
(ids) => {
  const Y = window.__yanki;
  const main = document.querySelector('main, [role="main"]');
  if (!main) return null;
  // Belge sırasında main'den önce gelen farklı duraklar (odak sırasından bağımsız).
  const seen = new Set();
  for (const id of ids) {
    const el = Y.get(id);
    if (!el || el.getRootNode() !== document || main.contains(el)) continue;
    if (main.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING) seen.add(id);
  }
  return seen.size;
}
"""

# Etkinleştirme politikası için öğe bilgisi (KLV-07, KLV-08).
PROBE_INFO = r"""
(id) => {
  const Y = window.__yanki;
  const el = Y.get(id);
  if (!el) return null;
  const tag = el.localName;
  const role = ((el.getAttribute('role') || '').trim().split(/\s+/)[0] || '').toLowerCase() || null;
  const type = (el.getAttribute('type') || '').toLowerCase();
  const inForm = !!el.closest('form') || !!el.form;
  const native = ['a', 'area', 'button', 'input', 'select', 'textarea', 'summary', 'iframe',
                  'audio', 'video', 'details', 'label'].includes(tag);
  const submitish = (tag === 'button' && ['', 'submit', 'reset'].includes(type) && inForm) ||
    (tag === 'input' && ['submit', 'reset', 'image'].includes(type));
  let hrefKind = null;
  if ((tag === 'a' || tag === 'area') && el.hasAttribute('href')) {
    const h = el.getAttribute('href').trim();
    hrefKind = h === '' ? 'empty' : /^javascript:/i.test(h) ? 'js'
      : (h === '#' || h === '#!') ? 'hash' : h.startsWith('#') ? 'anchor' : 'real';
  }
  let declared = (el.getAttribute('aria-haspopup') || '').toLowerCase() === 'dialog';
  const ctl = el.getAttribute('aria-controls');
  if (!declared && ctl) {
    for (const c of ctl.split(/\s+/)) {
      const t = document.getElementById(c);
      if (t && t.matches('dialog, [role="dialog"], [role="alertdialog"], [aria-modal="true"]')) {
        declared = true;
      }
    }
  }
  return {
    tag, role, type, native, in_form: inForm, submitish, href_kind: hrefKind, declared,
    onclick: el.hasAttribute('onclick'),
    pointer: getComputedStyle(el).cursor === 'pointer',
    contenteditable: el.isContentEditable,
  };
}
"""

# Fareyle tıklanabilir görünüp Tab ile erişilemeyen adaylar (KLV-06).
CLICK_CANDIDATES = r"""
(visited) => {
  const Y = window.__yanki;
  const seen = new Set(visited);
  const NATIVE = 'a[href], area[href], button, input, select, textarea, summary, iframe, ' +
    'label, audio[controls], video[controls], option';
  const ROLES = ['button', 'link', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab',
                 'checkbox', 'switch', 'radio', 'option', 'treeitem'];
  const out = [];
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (el.matches(NATIVE) || el.isContentEditable) continue;
    if (seen.has(Y.idOf(el)) || Y.isTabbable(el)) continue;
    const parent = el.parentElement;
    // Tıklama, odaklanabilir bir ataya kabarcıklanır; klavye kullanıcısı o ataya ulaşır.
    if (parent && parent.closest(NATIVE + ', [tabindex]:not([tabindex^="-"]), [contenteditable]')) {
      continue;
    }
    if (!Y.visibleEl(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const role = ((el.getAttribute('role') || '').trim().split(/\s+/)[0] || '').toLowerCase();
    const onclick = el.hasAttribute('onclick');
    const pointer = getComputedStyle(el).cursor === 'pointer' &&
      !(parent && getComputedStyle(parent).cursor === 'pointer');
    if (!onclick && !pointer && !ROLES.includes(role)) continue;
    // İçinde ziyaret edilmiş bir sekme durağı varsa klavyeyle erişim vardır.
    if ([...el.querySelectorAll('*')].some(d => seen.has(Y.idOf(d)) || Y.isTabbable(d))) continue;
    if (el.closest('form')) continue;  // temkinli politika
    out.push(Y.idOf(el));
  }
  return out;
}
"""

# Tıklama için öğeyi görünüm alanına getirir; tıklama noktasında öğenin
# kendisi (ya da bir alt öğesi) yoksa ``null`` döner.
CLICK_POINT = """
(id) => {
  const Y = window.__yanki;
  const el = Y.get(id);
  if (!el) return null;
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  if (!hit || !(hit === el || el.contains(hit))) return null;
  return [x, y];
}
"""

# Etki gözlemcisi: DOM değişimi, adres değişimi, yeni dialog (KLV-06/07/08).
WATCH_START = """
(minCover) => {
  const Y = window.__yanki;
  if (Y.obs) Y.obs.disconnect();
  Y.mut = 0;
  Y.obs = new MutationObserver((list) => { Y.mut += list.length; });
  Y.obs.observe(document.documentElement,
    { subtree: true, childList: true, attributes: true, characterData: true });
  const fixed = Y.fixedVisible();
  Y.watch = { url: location.href, fixed, minCover,
              dialogs: Y.dialogs(minCover, fixed).map(d => d.id) };
}
"""

WATCH_READ = """
() => {
  const Y = window.__yanki;
  if (!Y || !Y.watch) return null;
  if (Y.obs) { Y.obs.takeRecords().forEach(() => { Y.mut += 1; }); Y.obs.disconnect(); }
  const w = Y.watch;
  const strip = (u) => u.split('#')[0];
  const now = Y.dialogs(w.minCover, w.fixed);
  const fresh = now.filter(d => !w.dialogs.includes(d.id));
  return {
    mutations: Y.mut,
    url_changed: location.href !== w.url,
    hash_only: location.href !== w.url && strip(location.href) === strip(w.url),
    new_dialogs: fresh,
  };
}
"""

IN_DIALOG = """
(dialog) => {
  const Y = window.__yanki;
  const d = Y.get(dialog);
  const a = Y.deepActive();
  return !!d && !!a && Y.within(d, a.el);
}
"""

DIALOG_TABBABLES = """
(dialog) => {
  const Y = window.__yanki;
  const d = Y.get(dialog);
  if (!d) return [];
  return [...d.querySelectorAll('*')].filter(Y.isTabbable).map(Y.idOf);
}
"""

DIALOG_OPEN = """
(dialog) => {
  const Y = window.__yanki;
  const d = Y.get(dialog);
  if (!d || !d.isConnected || !Y.visibleEl(d)) return false;
  return d.localName !== 'dialog' || d.open;
}
"""

ACTIVE_ID = """
() => {
  const Y = window.__yanki;
  const a = Y.deepActive();
  return a ? Y.idOf(a.el) : null;
}
"""
