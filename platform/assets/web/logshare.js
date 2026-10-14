/* logshare.js — a single SSE log connection for all tabs (wave 5).
 *
 * Each tab with the log console in "Real time" competes for a browser lock
 * (Web Locks: `navigator.locks.request('hub-logs-leader')`). Whoever holds the lock is the
 * LEADER: it opens the only EventSource (no filter; each tab filters locally) and relays
 * each line to the other tabs through BroadcastChannel('hub-logs'). The others are
 * FOLLOWERS: they only listen to the channel and wait in the lock's queue. When the leader
 * tab closes (or pauses), the browser releases the lock and the next one in the queue takes
 * over by itself (failover) — no two connections at the same time and no duplicated line.
 * Without BroadcastChannel/Web Locks (old browser), each tab opens its own connection.
 *
 * Tested in node (`node --test platform/assets/web/tests/`), with
 * simulated BroadcastChannel/Web Locks.
 */
(function (global) {
  'use strict';

  /** A line passes the console filters (same rule as `LogFilter::matches`). */
  function matches(line, filter) {
    const app = filter.app || 'all';
    if (app !== 'all' && line.app !== app) return false;
    const user = (filter.user || '').trim().toLowerCase();
    return !user || String(line.user || '').toLowerCase().includes(user);
  }

  /**
   * @param {object} o
   * @param {() => {addEventListener: Function, close: Function, onerror?: any}} o.openSource
   * @param {(line: object) => void} o.onLine  new line (already from the shared stream)
   * @param {(s: {connected: boolean, role: string}) => void} [o.onStatus]
   * @param {any} [o.channel]  BroadcastChannel (default: global)
   * @param {any} [o.locks]    navigator.locks (default: global)
   */
  function createLogShare(o) {
    const name = o.name || 'hub-logs';
    // Name of the SSE event relayed (wave 9: the Inbox uses `inbox` in the same mechanism).
    const eventName = o.event || 'log';
    const mkChannel = o.channel !== undefined ? () => o.channel : () => (typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(name) : null);
    const locks = o.locks !== undefined ? o.locks : (typeof navigator !== 'undefined' && navigator.locks) || null;
    const status = (s) => { if (o.onStatus) o.onStatus(s); };
    let channel = null;
    let source = null;
    let role = 'parado';
    let release = null;
    let abort = null;
    let running = false;

    const setT = o.setTimeout || ((f, ms) => setTimeout(f, ms));
    const clearT = o.clearTimeout || ((h) => clearTimeout(h));
    // Leader reconnection when the EventSource closes for good (readyState CLOSED: 401,
    // expired session, hub restarting): increasing wait 1 s → 2 s → … → 30 s; after
    // `maxFailures` attempts without `ready`, the tab hands back the leadership (another tab,
    // perhaps with a valid session, takes over) and returns to the queue.
    const maxFailures = o.maxFailures || 5;
    let failures = 0;
    let retryTimer = null;
    let connected = false;

    function announce(c) {
      connected = c;
      status({ connected: c, role });
      if (channel) channel.postMessage({ t: 'status', connected: c });
    }

    function openLeaderSource() {
      source = o.openSource();
      source.addEventListener('ready', () => {
        failures = 0;
        announce(true);
      });
      source.addEventListener(eventName, (ev) => {
        const line = JSON.parse(ev.data);
        o.onLine(line);
        if (channel) channel.postMessage({ t: 'log', line });
      });
      source.onerror = () => {
        announce(false);
        // CLOSED (2): the browser does not retry on its own.
        if (source && source.readyState === 2) onClosed();
      };
    }

    function onClosed() {
      if (source) { source.close(); source = null; }
      if (!running || role !== 'líder') return;
      failures += 1;
      if (failures >= maxFailures && release) {
        // Hands back the leadership and returns to the queue.
        const r = release;
        release = null;
        role = 'seguidora';
        failures = 0;
        status({ connected: false, role });
        r();
        queue();
        return;
      }
      const delay = Math.min(30000, 1000 * 2 ** (failures - 1));
      retryTimer = setT(() => {
        retryTimer = null;
        if (running && role === 'líder' && !source) openLeaderSource();
      }, delay);
    }

    function becomeLeader() {
      role = 'líder';
      failures = 0;
      openLeaderSource();
      status({ connected: false, role });
    }

    function onMessage(ev) {
      const m = ev.data || {};
      if (role === 'líder') {
        // New tab asking for the current state of the connection.
        if (m.t === 'hello' && channel) channel.postMessage({ t: 'status', connected });
        return;
      }
      if (m.t === 'log') o.onLine(m.line);
      else if (m.t === 'status') status({ connected: m.connected, role });
    }

    function queue() {
      abort = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const opts = abort ? { signal: abort.signal } : {};
      locks.request(`${name}-leader`, opts, () => {
        if (!running) return undefined;
        becomeLeader();
        // The lock stays held until this tab stops, closes or hands back the leadership.
        return new Promise((resolve) => { release = resolve; });
      }).catch(() => { /* request cancelled on stop */ });
    }

    function start() {
      if (running) return;
      running = true;
      channel = mkChannel();
      if (!channel || !locks) {
        // No support: own connection.
        becomeLeader();
        return;
      }
      if (channel.addEventListener) channel.addEventListener('message', onMessage);
      else channel.onmessage = onMessage;
      role = 'seguidora';
      // Only "connected" when a leader confirms (asks for the current state).
      status({ connected: false, role });
      channel.postMessage({ t: 'hello' });
      queue();
    }

    function stop() {
      if (!running) return;
      running = false;
      if (retryTimer) { clearT(retryTimer); retryTimer = null; }
      if (source) { source.close(); source = null; }
      if (release) { release(); release = null; }
      if (abort) { abort.abort(); abort = null; }
      if (channel) {
        if (channel.removeEventListener) channel.removeEventListener('message', onMessage);
        if (channel.close) channel.close();
        channel = null;
      }
      role = 'parado';
      status({ connected: false, role });
    }

    return { start, stop, role: () => role, running: () => running };
  }

  const api = { createLogShare, matches };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.HubLogShare = api;
})(typeof window !== 'undefined' ? window : globalThis);
