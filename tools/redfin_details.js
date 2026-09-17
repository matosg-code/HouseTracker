// Run in a Chrome tab that is on any www.redfin.com page (DevTools console, or
// via Claude in Chrome). Browsers will not let a redfin.com page talk to a local
// server, so results are handed back through the clipboard.
//
//   1. window.__enrichStart(items)   items = output of `python -m tracker.enrich todo`
//      Fetches every detail endpoint per listing in the background.
//   2. window.__enrichStatus()       -> {done, total, failed, running}
//   3. window.__enrichFlush()        copies finished results to the clipboard as a
//      JSON array and clears them. Then: python -m tracker.enrich ingest --clipboard
//
// The tab must be focused for the clipboard write (click on the page first).
(() => {
  const ENDPOINTS = ["mainHouseInfoPanelInfo", "belowTheFold", "aboveTheFold", "avm", "propertyParcelInfo"];
  const GAP_MS = 350;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const S = (window.__enrichState = window.__enrichState || { results: [], failed: [], done: 0, total: 0, running: false });

  window.__enrichStart = (items) => {
    if (S.running) return "already running";
    S.total += items.length; S.running = true;
    (async () => {
      for (const item of items) {
        const raw = {}; let ok = true;
        for (const ep of ENDPOINTS) {
          try {
            const r = await fetch(`/stingray/api/home/details/${ep}?propertyId=${item.property_id}&accessLevel=1`, { credentials: "include" });
            const text = await r.text();
            raw[ep] = text.startsWith("{}&&") ? JSON.parse(text.slice(4)) : { _status: r.status, _text: text.slice(0, 300) };
            if (r.status !== 200) ok = false;
          } catch (e) { raw[ep] = { _error: String(e) }; ok = false; }
          await sleep(GAP_MS);
        }
        if (ok) S.results.push({ ...item, fetched_at: new Date().toISOString(), raw });
        else S.failed.push({ address: item.address, property_id: item.property_id, statuses: Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v._status || v._error || 200])) });
        S.done++;
      }
      S.running = false;
    })();
    return `started ${items.length}`;
  };

  window.__enrichStatus = () => ({ done: S.done, total: S.total, ready: S.results.length, failed: S.failed, running: S.running });

  window.__enrichFlush = async () => {
    const out = S.results.splice(0, S.results.length);
    const text = JSON.stringify(out);
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove();
      if (!ok) { S.results.unshift(...out); return { error: "clipboard write failed: " + e }; }
    }
    return { copied: out.length, bytes: text.length };
  };
  return "enrich helpers installed";
})();
