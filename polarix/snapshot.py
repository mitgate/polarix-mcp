"""Snapshot helpers — DOM inventory JS and selector index builder."""

from __future__ import annotations

from playwright.async_api import Page

_INVENTORY_JS = """
() => {
    const qaMap = {};
    document.querySelectorAll('[data-qa]').forEach(el => {
        const qa = el.getAttribute('data-qa');
        if (!qaMap[qa]) {
            qaMap[qa] = { qa, tag: el.tagName, count: 0, texts: [],
                          type: el.getAttribute('type') || null };
        }
        qaMap[qa].count++;
        const t = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
        if (t && !qaMap[qa].texts.includes(t)) qaMap[qa].texts.push(t);
    });

    const forms = [];
    document.querySelectorAll('form,[role=form]').forEach(form => {
        const inputs = [...form.querySelectorAll('input,textarea,select')].map(inp => ({
            tag: inp.tagName, id: inp.id || null, name: inp.name || null,
            type: inp.type || null, placeholder: inp.placeholder || null,
            data_qa: inp.getAttribute('data-qa') || null, required: inp.required,
        }));
        if (inputs.length) forms.push({ inputs });
    });
    const looseInputs = [...document.querySelectorAll(
        'input:not(form input),textarea:not(form textarea)'
    )].map(inp => ({
        tag: inp.tagName, id: inp.id || null, name: inp.name || null,
        type: inp.type || null, placeholder: inp.placeholder || null,
        data_qa: inp.getAttribute('data-qa') || null,
    }));
    if (looseInputs.length) forms.push({ loose: true, inputs: looseInputs });

    const links = [];
    document.querySelectorAll('a[href]').forEach(a => {
        try {
            const href = new URL(a.href, window.location.href);
            if (href.hostname === window.location.hostname) {
                const p = href.pathname + (href.search || '');
                if (!links.includes(p) && p !== window.location.pathname) links.push(p);
            }
        } catch (e) {}
    });

    const buttons = [];
    document.querySelectorAll('button:not([data-qa]),[role=button]:not([data-qa])').forEach(btn => {
        const t = (btn.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
        if (t && !buttons.find(b => b.text === t)) buttons.push({ text: t, tag: btn.tagName });
    });

    const seen = new Set();
    const triggers = [];
    document.querySelectorAll('[data-qa]').forEach(el => {
        const qa = el.getAttribute('data-qa');
        if (seen.has(qa)) return;
        const ql = qa.toLowerCase();
        const interactive = (
            ql.includes('trigger') || ql.includes('button') || ql.includes('toggle') ||
            ql.includes('tab') || ql.includes('menu') || ql.includes('dropdown') ||
            el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' ||
            el.hasAttribute('aria-expanded') || el.hasAttribute('aria-haspopup')
        );
        if (interactive) { seen.add(qa); triggers.push({ qa, tag: el.tagName }); }
    });

    return {
        url: window.location.href,
        path: window.location.pathname + (window.location.search || ''),
        title: document.title,
        data_qa_elements: Object.values(qaMap),
        forms,
        navigation_links: links,
        buttons_no_qa: buttons,
        interactive_triggers: triggers,
    };
}
"""


async def _snapshot(page: Page) -> dict:
    return await page.evaluate(_INVENTORY_JS)


def _selector_index(pages: list[dict]) -> dict:
    index: dict = {}
    for pg in pages:
        for el in pg.get("data_qa_elements", []):
            qa = el["qa"]
            if qa not in index:
                index[qa] = {"count_total": 0, "pages_found": []}
            index[qa]["count_total"] += el["count"]
            if pg["path"] not in index[qa]["pages_found"]:
                index[qa]["pages_found"].append(pg["path"])
    return index
