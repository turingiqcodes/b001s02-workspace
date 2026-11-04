'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createEnv, parseRaw, SECRET } = require('./helpers/gas');

// ---------------- security & protocol (AT-25, AT-26, NFR-02/03/06/07) ----------------

test('rejects a wrong or missing secret', () => {
  const env = createEnv();
  for (const secret of ['wrong-secret-0123456789abcdef', '', undefined, 123]) {
    const res = env.post({ secret, operation: 'get_profile', args: {} });
    assert.equal(res.ok, false);
    assert.equal(res.error.code, 'unauthorized');
  }
  assert.equal(env.calls.length, 0, 'no Google call made');
});

test('refuses to run when BRIDGE_SECRET is not set or too short', () => {
  for (const secret of [null, 'short']) {
    const env = createEnv({ secret });
    const res = env.call('get_profile', {}, secret || '');
    assert.equal(res.error.code, 'bridge_not_configured');
  }
});

test('rejects unknown operations, including inherited property names', () => {
  const env = createEnv();
  for (const op of ['delete_forever', 'toString', '__proto__', 'constructor', 42]) {
    const res = env.call(op);
    assert.equal(res.ok, false);
    assert.equal(res.error.code, 'unknown_operation');
  }
});

test('rejects non-JSON and malformed requests', () => {
  const env = createEnv();
  assert.equal(env.post('not json').error.code, 'invalid_request');
  assert.equal(env.post({ secret: SECRET, operation: 'get_profile', args: [] }).error.code, 'invalid_arguments');
});

test('allow-list matches the backend and has no permanent delete', () => {
  const env = createEnv();
  const ops = Array.from(env.context.ALLOWED_OPERATIONS).sort();
  const fs = require('node:fs');
  const path = require('node:path');
  const py = fs.readFileSync(path.join(__dirname, '..', '..', 'backend', 'app', 'services', 'bridge.py'), 'utf8');
  const block = py.slice(py.indexOf('ALLOWED_OPERATIONS = frozenset('), py.indexOf('class BridgeClient'));
  const backendOps = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual(ops, backendOps);
  assert.ok(!ops.some((o) => /permanent|empty_trash|delete_email|delete_file/.test(o)));
  // No permanent-delete calls of any kind (Gmail API delete, Drive removeFile/delete, emptyTrash).
  const code = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');
  assert.ok(!/Messages\.(remove|delete)\(|removeFile\(|\.delete\(|emptyTrash|Drive\.Files\.remove/.test(code));
  // Only built-in services: no advanced-service globals.
  assert.ok(!/\b(Gmail|Calendar|Drive)\.(Users|Events|Freebusy|Files)\b/.test(code));
});

test('Google errors become readable error objects', () => {
  const env = createEnv();
  const res = env.call('read_email', { message_id: 'missing' });
  assert.deepEqual(res, { ok: false, error: { code: 'not_found', message: 'Email was not found.' } });
  // Raw Google exceptions are mapped too.
  assert.equal(env.call('get_drive_file', { file_id: 'missing' }).error.message, 'The requested item was not found.');
});

test('validates arguments before calling Google', () => {
  const env = createEnv();
  const cases = [
    ['read_email', {}],
    ['get_latest_emails', { max_results: 500 }],
    ['send_email', { to: ['not-an-email'], subject: 's', body: 'b' }],
    ['send_email', { to: ['a@b.co'], subject: 'hi\r\nBcc: evil@x.com', body: 'b' }],
    ['create_calendar_event', { title: 't', start: '2026-11-05T10:00:00', end: '2026-11-05T11:00:00+05:30' }],
    ['update_calendar_event', { event_id: 'e1@google.com', send_updates: 'everyone', title: 'x' }],
    ['update_calendar_event', { event_id: 'e1@google.com' }],
  ];
  for (const [op, args] of cases) {
    const res = env.call(op, args);
    assert.equal(res.ok, false, op);
    assert.equal(res.error.code, 'invalid_arguments', `${op}: ${res.error.message}`);
  }
  assert.ok(!env.calls.some((c) => /sendEmail|createEvent|setTitle|createDraft/.test(c.name)));
});

test('doGet does not expose data', () => {
  const env = createEnv();
  const out = JSON.parse(env.context.doGet().content);
  assert.deepEqual(out, { ok: true, service: 'email-manager-bridge' });
});

test('get_profile returns address and time zone', () => {
  const env = createEnv();
  assert.deepEqual(env.call('get_profile').result, { email: 'me@example.com', time_zone: 'Asia/Kolkata' });
});

