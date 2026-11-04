'use strict';
// Runs Code.gs inside a Node vm context with in-memory fakes of the Google services it uses.

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CODE = fs.readFileSync(path.join(__dirname, '..', '..', 'Code.gs'), 'utf8');
const SECRET = 'test-secret-0123456789abcdef';

function b64url(str) {
  return Buffer.from(str, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function notFound(what) {
  return new Error('API call to ' + what + ' failed with error: Requested entity was not found.');
}

function clone(x) { return x === undefined ? undefined : JSON.parse(JSON.stringify(x)); }

/** A simple text/plain message, or multipart/mixed when attachments are given. */
function makeMessage(o) {
  const headers = [
    { name: 'From', value: o.from || 'Alice <alice@example.com>' },
    { name: 'To', value: o.to || 'me@example.com' },
    { name: 'Subject', value: o.subject || 'Hello' },
    { name: 'Date', value: 'Mon, 6 Jan 2025 10:00:00 +0000' },
    { name: 'Message-ID', value: '<' + o.id + '@mail.example.com>' },
  ];
  if (o.cc) headers.push({ name: 'Cc', value: o.cc });
  if (o.replyTo) headers.push({ name: 'Reply-To', value: o.replyTo });
  const textPart = { partId: '0', mimeType: 'text/plain', filename: '', body: { data: b64url(o.body || 'Body text'), size: 9 } };
  let payload;
  if (o.attachments && o.attachments.length) {
    payload = {
      mimeType: 'multipart/mixed', headers: headers, body: { size: 0 },
      parts: [textPart].concat(o.attachments.map(function (a, i) {
        return { partId: String(i + 1), mimeType: a.mimeType || 'application/pdf', filename: a.filename,
          body: { attachmentId: 'att_' + o.id + '_' + (i + 1), size: a.size } };
      })),
    };
  } else {
    payload = Object.assign({ headers: headers }, textPart, { partId: '' });
  }
  return {
    id: o.id, threadId: o.threadId || 'thread_' + o.id, labelIds: o.labelIds || ['INBOX', 'UNREAD'],
    snippet: o.snippet || 'Body text', internalDate: String(Date.UTC(2025, 0, 6, 10, 0, 0)), payload: payload,
  };
}

function createGas(opts) {
  opts = opts || {};
  const state = {
    me: 'me@example.com',
    props: opts.props !== undefined ? opts.props : { BRIDGE_SHARED_SECRET: SECRET },
    labels: [
      { id: 'INBOX', name: 'INBOX', type: 'system' },
      { id: 'UNREAD', name: 'UNREAD', type: 'system' },
      { id: 'STARRED', name: 'STARRED', type: 'system' },
      { id: 'IMPORTANT', name: 'IMPORTANT', type: 'system' },
      { id: 'SPAM', name: 'SPAM', type: 'system' },
      { id: 'TRASH', name: 'TRASH', type: 'system' },
      { id: 'SENT', name: 'SENT', type: 'system' },
      { id: 'Label_1', name: 'Receipts', type: 'user' },
    ],
    messages: {},
    attachmentData: {},
    drafts: {},
    events: {},
    files: { root: { id: 'root', name: 'My Drive', mimeType: 'application/vnd.google-apps.folder', parents: [] } },
    calls: [],
    sent: [],
    logs: [],
    counter: 0,
  };
  const next = function (p) { state.counter += 1; return p + state.counter; };
  const rec = function (name, args) { state.calls.push({ name: name, args: clone(args) }); };

  const Gmail = {
    Users: {
      getProfile: function () { return { emailAddress: state.me }; },
      Labels: {
        list: function () { return { labels: clone(state.labels) }; },
        create: function (res) { rec('Labels.create', res); const l = { id: next('Label_'), name: res.name, type: 'user' }; state.labels.push(l); return l; },
      },
      Messages: {
        list: function (user, args) { rec('Messages.list', args); const ids = Object.keys(state.messages); return { messages: ids.slice(0, args.maxResults).map(function (id) { return { id: id }; }), resultSizeEstimate: ids.length }; },
        get: function (user, id, args) {
          const m = state.messages[id];
          if (!m) throw notFound('gmail.users.messages.get');
          const c = clone(m);
          if (args && args.format === 'metadata') delete c.payload.parts;
          return c;
        },
        send: function (msg) { rec('Messages.send', msg); state.sent.push(msg); return { id: next('sent_'), threadId: msg.threadId || next('thread_') }; },
        batchModify: function (req) { rec('Messages.batchModify', req); },
        Attachments: {
          get: function (user, msgId, attId) { return { data: state.attachmentData[attId] || b64url('file-bytes-' + attId) }; },
        },
      },
      Drafts: {
        list: function () { return { drafts: Object.keys(state.drafts).map(function (id) { return { id: id }; }) }; },
        get: function (user, id) { const d = state.drafts[id]; if (!d) throw notFound('gmail.users.drafts.get'); return clone(d); },
        create: function (res) {
          rec('Drafts.create', res);
          const id = next('draft_');
          state.drafts[id] = { id: id, message: { id: next('msg_'), threadId: res.message.threadId, raw: res.message.raw, payload: { headers: [] } } };
          return { id: id, message: { id: state.drafts[id].message.id } };
        },
        update: function (res, user, id) { rec('Drafts.update', { res: res, id: id }); return { id: id, message: { id: next('msg_') } }; },
        send: function (res) { rec('Drafts.send', res); if (!state.drafts[res.id]) throw notFound('gmail.users.drafts.send'); return { id: next('sent_'), threadId: 't1' }; },
      },
    },
  };

  const Calendar = {
    Events: {
      list: function (cal, args) { rec('Events.list', args); return { timeZone: 'Asia/Kolkata', items: Object.keys(state.events).map(function (k) { return clone(state.events[k]); }) }; },
      get: function (cal, id) { const e = state.events[id]; if (!e) throw notFound('calendar.events.get'); return clone(e); },
      insert: function (res, cal, args) { rec('Events.insert', { res: res, args: args }); const id = next('ev_'); state.events[id] = Object.assign({ id: id, status: 'confirmed', organizer: { self: true } }, clone(res)); return clone(state.events[id]); },
      patch: function (res, cal, id, args) { rec('Events.patch', { res: res, id: id, args: args }); Object.assign(state.events[id], clone(res)); return clone(state.events[id]); },
      remove: function (cal, id, args) { rec('Events.remove', { id: id, args: args }); delete state.events[id]; },
    },
  };

  const Drive = {
    Files: {
      list: function (args) { rec('Files.list', args); return { files: (opts.driveListResult || []).map(clone) }; },
      get: function (id) { const f = state.files[id]; if (!f) throw notFound('drive.files.get'); return clone(f); },
      create: function (res, blob, args) {
        rec('Files.create', { res: res, blob: blob ? { name: blob.getName(), type: blob.getContentType(), text: blob.getDataAsString() } : null, args: args });
        const id = next('file_');
        state.files[id] = Object.assign({ id: id, webViewLink: 'https://drive.google.com/' + id, parents: res.parents || ['root'] }, clone(res));
        return clone(state.files[id]);
      },
      update: function (res, id, blob, args) {
        rec('Files.update', { id: id, args: args });
        const f = state.files[id];
        const remove = (args.removeParents || '').split(',').filter(String);
        f.parents = f.parents.filter(function (p) { return remove.indexOf(p) === -1; }).concat([args.addParents]);
        return clone(f);
      },
    },
  };

  const Utilities = {
    Charset: { UTF_8: 'UTF-8' },
    base64Encode: function (data) { return Buffer.from(typeof data === 'string' ? data : Uint8Array.from(data)).toString('base64'); },
    base64EncodeWebSafe: function (data) { return Buffer.from(typeof data === 'string' ? data : Uint8Array.from(data)).toString('base64').replace(/\+/g, '-').replace(/\//g, '_'); },
    base64DecodeWebSafe: function (s) { return Array.from(Buffer.from(String(s).replace(/-/g, '+').replace(/_/g, '/'), 'base64')); },
    newBlob: function (data, type, name) {
      const buf = typeof data === 'string' ? Buffer.from(data, 'utf8') : Buffer.from(Uint8Array.from(data));
      return { getDataAsString: function () { return buf.toString('utf8'); }, getBytes: function () { return Array.from(buf); }, getName: function () { return name; }, getContentType: function () { return type; } };
    },
  };

  const sandbox = {
    Gmail: Gmail, Calendar: Calendar, Drive: Drive, Utilities: Utilities,
    PropertiesService: { getScriptProperties: function () { return { getProperty: function (k) { return Object.prototype.hasOwnProperty.call(state.props, k) ? state.props[k] : null; } }; } },
    ContentService: {
      MimeType: { JSON: 'application/json' },
      createTextOutput: function (content) { return { content: content, mime: null, setMimeType: function (m) { this.mime = m; return this; }, getContent: function () { return this.content; } }; },
    },
    Session: { getScriptTimeZone: function () { return 'Etc/UTC'; } },
    console: { log: function () { state.logs.push(Array.from(arguments).join(' ')); }, error: function () { state.logs.push('ERROR ' + Array.from(arguments).join(' ')); } },
  };
  const ctx = vm.createContext(sandbox);
  vm.runInContext(CODE, ctx, { filename: 'Code.gs' });

  function post(action, params, secret) {
    const body = { secret: secret === undefined ? SECRET : secret, action: action, params: params };
    const out = ctx.doPost({ postData: { contents: JSON.stringify(body) } });
    return JSON.parse(out.getContent());
  }
  function postRaw(contents) { return JSON.parse(ctx.doPost({ postData: { contents: contents } }).getContent()); }
  function ok(action, params) {
    const r = post(action, params);
    if (!r.ok) throw new Error('Expected ok for ' + action + ', got ' + JSON.stringify(r.error));
    return r.data;
  }
  function addMessage(o) { state.messages[o.id] = makeMessage(o); return state.messages[o.id]; }
  function calls(name) { return state.calls.filter(function (c) { return c.name === name; }); }

  return { ctx: ctx, state: state, post: post, postRaw: postRaw, ok: ok, addMessage: addMessage, calls: calls, run: function (expr) { return vm.runInContext(expr, ctx); } };
}

/** Decode a Gmail API raw (base64url) message into headers and decoded parts. */
function decodeRaw(raw) {
  const text = Buffer.from(raw.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
  const split = text.indexOf('\r\n\r\n');
  const headerText = text.slice(0, split);
  const headers = {};
  headerText.split('\r\n').forEach(function (line) {
    const i = line.indexOf(':');
    headers[line.slice(0, i).toLowerCase()] = line.slice(i + 1).trim();
  });
  const rest = text.slice(split + 4);
  let body;
  const parts = [];
  const m = /boundary="([^"]+)"/.exec(headers['content-type'] || '');
  if (m) {
    rest.split('--' + m[1]).slice(1).forEach(function (chunk) {
      if (chunk.indexOf('--') === 0) return;
      const j = chunk.indexOf('\r\n\r\n');
      const h = chunk.slice(0, j);
      const data = chunk.slice(j + 4).replace(/\r\n/g, '');
      parts.push({ headers: h, data: data, text: Buffer.from(data, 'base64').toString('utf8') });
    });
    body = parts[0].text;
  } else {
    body = Buffer.from(rest.replace(/\r\n/g, ''), 'base64').toString('utf8');
  }
  return { headers: headers, body: body, parts: parts, text: text };
}

module.exports = { createGas: createGas, decodeRaw: decodeRaw, b64url: b64url, SECRET: SECRET };
