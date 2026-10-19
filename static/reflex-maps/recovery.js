/* Autosave for the Reflex map page: the last few snapshots of the edited map, as the text Save .map writes, kept in
   the browser's IndexedDB so a crash or a closed tab loses at most a minute. `rotate` is the rule and runs in Node
   (tests/reflex_maps.test.cjs); the rest is the store. Every store call settles, never throws: where IndexedDB is
   missing or refuses (a private window, blocked site data), saving does nothing and the list is empty. */
(function (exports) {
  'use strict';
  const KEEP = 5;
  /* A snapshot {id, name, time, brushes, text} put in front of `list` (newest first), keeping `keep`; returns
     {list, dropped} with the ids that fell off. The same text of the same map as the newest is not kept again. */
  function rotate(list, snapshot, keep = KEEP) {
    if (list[0] && list[0].name === snapshot.name && list[0].text === snapshot.text) return {list, dropped: []};
    const next = [snapshot, ...list];
    return {list: next.slice(0, keep), dropped: next.slice(keep).map(old => old.id)};
  }

  const DB = 'skinner-reflex-maps', STORE = 'snapshots';
  let opened = null;
  // A request as a promise of its result, or of null when it fails.
  const settle = request => new Promise(resolve => { request.onsuccess = () => resolve(request.result); request.onerror = () => resolve(null); });
  function open() {
    if (!opened) opened = new Promise(resolve => {
      try {
        const request = indexedDB.open(DB, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(STORE, {keyPath: 'id'});
        request.onblocked = () => resolve(null);
        settle(request).then(resolve);
      } catch (_) { resolve(null); }
    });
    return opened;
  }
  async function store(mode, use) {
    const db = await open();
    if (!db) return null;
    try {
      const transaction = db.transaction(STORE, mode);
      const done = new Promise(resolve => { transaction.oncomplete = () => resolve(true); transaction.onerror = transaction.onabort = () => resolve(false); });
      const result = await use(transaction.objectStore(STORE));
      return await done ? result : null;
    } catch (_) { return null; }
  }
  // Every snapshot, newest first ([] without storage).
  async function list() {
    const all = await store('readonly', snapshots => settle(snapshots.getAll()));
    return (all || []).sort((a, b) => b.time - a.time);
  }
  // Keeps `snapshot` by the rule; true when it was stored.
  async function save(snapshot) {
    const {list: kept, dropped} = rotate(await list(), snapshot);
    if (kept[0] !== snapshot) return false;
    return !!await store('readwrite', snapshots => {
      snapshots.put(snapshot);
      for (const id of dropped) snapshots.delete(id);
      return true;
    });
  }

  exports.ReflexRecovery = {KEEP, rotate, list, save};
})(typeof module !== 'undefined' ? module.exports : window);
