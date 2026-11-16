"""Sayfadan öğe ve bağlam toplama (TAR-03).

Görseller, düğmeler, bağlantılar ve form alanları belge sırasıyla toplanır.
Her öğe için erişilebilir ad, en yakın başlık ve çevreleyen metin saklanır.

Seçici ve erişilebilir ad, sayfaya enjekte edilmiş axe-core ile hesaplanır
(``axe.utils.getSelector``, ``axe.commons.text.accessibleText``). Böylece
öğe seçicileri kural bulgularının seçicileriyle aynı biçimdedir.
Bu nedenle ``inject_axe`` bu modülden önce çağrılmalıdır.

Sınır: Faz 0'da yalnızca ana belge (iframe ve shadow DOM dışı) taranır.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

from yanki.core.schema import PageElement

_COLLECT_JS = r"""
(maxChars) => {
  const IMAGE = 'img, input[type="image" i], [role="img"]';
  const BUTTON = 'button, summary, [role="button"], ' +
    'input[type="button" i], input[type="submit" i], input[type="reset" i]';
  const LINK = 'a[href], area[href], [role="link"]';
  const FIELD = 'select, textarea, ' +
    'input:not([type="hidden" i]):not([type="button" i]):not([type="submit" i])' +
    ':not([type="reset" i]):not([type="image" i]), ' +
    '[role="textbox"], [role="searchbox"], [role="combobox"], [role="listbox"], ' +
    '[role="checkbox"], [role="radio"], [role="switch"], [role="slider"], [role="spinbutton"]';
  const KINDS = [['image', IMAGE], ['button', BUTTON], ['link', LINK], ['form_field', FIELD]];
  const ALL = KINDS.map(k => k[1]).join(', ');
  const HEADING = 'h1, h2, h3, h4, h5, h6, [role="heading"]';
  const BLOCK = 'figure, p, li, td, th, dd, dt, blockquote, label, fieldset, form, ' +
    'section, article, aside, nav, header, footer, main, div';

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s) => s.length > maxChars ? s.slice(0, maxChars - 1) + '…' : s;

  axe.setup(document);
  try {
    const headings = [...document.querySelectorAll(HEADING)];
    const nearestHeading = (el) => {
      let found = null;
      for (const h of headings) {
        if (h === el || h.contains(el)) continue;
        if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) found = h;
        else break;
      }
      return found;
    };
    const headingLevel = (h) => {
      const m = /^h([1-6])$/.exec(h.localName);
      if (m) return Number(m[1]);
      const lvl = Number(h.getAttribute('aria-level'));
      return lvl >= 1 && lvl <= 6 ? lvl : 2;
    };
    const surrounding = (el) => {
      const own = norm(el.innerText || el.getAttribute('alt') || '');
      let node = el.parentElement;
      while (node && node !== document.body) {
        if (node.matches(BLOCK)) {
          const text = norm(node.innerText);
          if (text && text !== own) return clip(text);
        }
        node = node.parentElement;
      }
      return '';
    };
    const visible = (el) => {
      if (typeof el.checkVisibility === 'function') {
        return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      }
      return el.getClientRects().length > 0;
    };
    const CONTROL = 'a[href], area[href], button, [role="button"], [role="link"]';
    // Görselin alt dışındaki metin alternatifi (ALT-01): aria-labelledby, aria-label, title.
    const textAlternative = (el) => {
      const ids = (el.getAttribute('aria-labelledby') || '').trim();
      if (ids) {
        const text = norm(ids.split(/\s+/).map(id => {
          const ref = document.getElementById(id);
          return ref ? (ref.innerText || ref.textContent || '') : '';
        }).join(' '));
        if (text) return text;
      }
      const label = norm(el.getAttribute('aria-label'));
      if (label) return label;
      const title = norm(el.getAttribute('title'));
      if (title) return title;
      // <svg role="img"><title>…</title></svg>: SVG'nin doğrudan <title> çocuğu adıdır.
      if (el.namespaceURI === 'http://www.w3.org/2000/svg') {
        const t = [...el.children].find(c => c.localName === 'title');
        const text = t ? norm(t.textContent) : '';
        if (text) return text;
      }
      return null;
    };

    const out = [];
    for (const el of document.querySelectorAll(ALL)) {
      const kind = KINDS.find(k => el.matches(k[1]))[0];
      const r = el.getBoundingClientRect();
      const h = nearestHeading(el);
      let name = '';
      try { name = norm(axe.commons.text.accessibleText(el)); } catch (e) { name = ''; }
      const item = {
        kind,
        selector: axe.utils.getSelector(el),
        tag: el.localName,
        role: el.getAttribute('role'),
        accessible_name: name,
        visible: visible(el),
        bbox: (r.width || r.height) ? {
          x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
          width: Math.round(r.width), height: Math.round(r.height),
        } : null,
        context: {
          nearest_heading: h ? clip(norm(h.innerText || h.textContent)) : null,
          heading_level: h ? headingLevel(h) : null,
          surrounding_text: surrounding(el),
        },
        lang: (el.closest('[lang]') || {getAttribute: () => null}).getAttribute('lang') || null,
        aria_hidden: !!el.closest('[aria-hidden="true" i]'),
        in_control: !!(el.parentElement && el.parentElement.closest(CONTROL)),
      };
      if (kind === 'image') {
        item.alt = el.getAttribute('alt');
        item.label = textAlternative(el);
        item.src = el.currentSrc || el.src || el.getAttribute('src');
        if (el.localName === 'img') item.natural_width = el.naturalWidth;
      }
      if (kind === 'link') item.href = el.href || el.getAttribute('href');
      if (el.localName === 'input') {
        item.input_type = (el.getAttribute('type') || 'text').toLowerCase();
      }
      out.push(item);
    }
    return out;
  } finally {
    axe.teardown();
  }
}
"""


async def collect_elements(page: Page, *, max_chars: int) -> list[PageElement]:
    raw: list[dict[str, Any]] = await page.evaluate(_COLLECT_JS, max_chars)
    return [PageElement.model_validate(item) for item in raw]
