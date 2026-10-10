/* logshare.js — uma única conexão SSE de logs para todas as abas (onda 5).
 *
 * Cada aba com o console de logs em "Tempo real" disputa uma trava do navegador
 * (Web Locks: `navigator.locks.request('hub-logs-leader')`). Quem tem a trava é o
 * LÍDER: abre o único EventSource (sem filtro; cada aba filtra localmente) e repassa
 * cada linha às outras abas por BroadcastChannel('hub-logs'). As demais são
 * SEGUIDORAS: só escutam o canal e ficam na fila da trava. Quando a aba líder fecha
 * (ou pausa), o navegador libera a trava e a próxima da fila assume sozinha
 * (failover) — sem duas conexões ao mesmo tempo e sem linha duplicada.
 * Sem BroadcastChannel/Web Locks (navegador antigo), cada aba abre a sua conexão.
 *
 * Testado em node (`node --test platform/assets/web/tests/`), com
 * BroadcastChannel/Web Locks simulados.
 */
(function (global) {
  'use strict';

  /** Linha passa pelos filtros do console (mesma regra de `LogFilter::matches`). */
  function matches(line, filter) {
    const app = filter.app || 'all';
    if (app !== 'all' && line.app !== app) return false;
    const user = (filter.user || '').trim().toLowerCase();
    return !user || String(line.user || '').toLowerCase().includes(user);
  }

  /**
   * @param {object} o
   * @param {() => {addEventListener: Function, close: Function, onerror?: any}} o.openSource
   * @param {(line: object) => void} o.onLine  linha nova (já do stream compartilhado)
   * @param {(s: {connected: boolean, role: string}) => void} [o.onStatus]
   * @param {any} [o.channel]  BroadcastChannel (padrão: global)
   * @param {any} [o.locks]    navigator.locks (padrão: global)
   */
  function createLogShare(o) {
    const name = o.name || 'hub-logs';
    // Nome do evento SSE repassado (onda 9: a Inbox usa `inbox` no mesmo mecanismo).
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
    // Reconexão da líder quando o EventSource fecha de vez (readyState CLOSED: 401,
    // sessão vencida, hub reiniciando): espera crescente 1 s → 2 s → … → 30 s; depois
    // de `maxFailures` tentativas sem `ready`, a aba devolve a liderança (outra aba,
    // talvez com sessão válida, assume) e volta para a fila.
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
        // CLOSED (2): o navegador não tenta de novo sozinho.
        if (source && source.readyState === 2) onClosed();
      };
    }

    function onClosed() {
      if (source) { source.close(); source = null; }
      if (!running || role !== 'líder') return;
      failures += 1;
      if (failures >= maxFailures && release) {
        // Devolve a liderança e volta para a fila.
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
        // Aba nova pedindo o estado atual da conexão.
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
        // A trava fica presa até esta aba parar, fechar ou devolver a liderança.
        return new Promise((resolve) => { release = resolve; });
      }).catch(() => { /* pedido cancelado ao parar */ });
    }

    function start() {
      if (running) return;
      running = true;
      channel = mkChannel();
      if (!channel || !locks) {
        // Sem suporte: conexão própria.
        becomeLeader();
        return;
      }
      if (channel.addEventListener) channel.addEventListener('message', onMessage);
      else channel.onmessage = onMessage;
      role = 'seguidora';
      // Só "conectada" quando uma líder confirmar (pergunta o estado atual).
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
