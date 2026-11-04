'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createGas, SECRET } = require('./helpers/gas');

test('doGet answers with a plain status message', () => {
  const gas = createGas();
  assert.match(gas.ctx.doGet().getContent(), /bridge is running/);
});

test('rejects a body that is not JSON', () => {
  const r = createGas().postRaw('not json');
  assert.equal(r.ok, false);
  assert.equal(r.error.code, 'invalid_params');
});

test('reports not_configured when the script property is missing', () => {
  const r = createGas({ props: {} }).post('ping', {});
  assert.equal(r.error.code, 'not_configured');
  assert.match(r.error.message, /BRIDGE_SHARED_SECRET/);
});

test('rejects a wrong, missing or differently sized secret', () => {
  const gas = createGas();
  for (const s of ['wrong-secret-0123456789abcdef', '', SECRET + 'x', 42]) {
    const r = gas.post('ping', {}, s);
    assert.equal(r.error.code, 'unauthorized');
    assert.equal(r.error.message, 'Invalid shared secret.');
  }
  assert.equal(gas.state.calls.length, 0, 'no Google call is made before authentication');
});

test('rejects unknown actions, including inherited object properties', () => {
  const gas = createGas();
  for (const a of ['delete_email', 'toString', '__proto__', 'constructor']) {
    assert.equal(gas.post(a, {}).error.code, 'unknown_action');
  }
});

test('ping returns the account, time zone and current time', () => {
  const data = createGas().ok('ping', {});
  assert.equal(data.ok, true);
  assert.equal(data.email, 'me@example.com');
  assert.equal(data.timezone, 'Asia/Kolkata');
  assert.ok(!isNaN(Date.parse(data.time)));
});

test('the bridge has no delete or trash operations for email or files', () => {
  const gas = createGas();
  const actions = Object.keys(gas.run('ACTIONS'));
  assert.ok(actions.length >= 22);
  for (const a of actions) assert.doesNotMatch(a, /delete|trash|remove|purge/);
  const code = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');
  assert.doesNotMatch(code, /Messages\.(trash|delete|batchDelete)|Threads\.|Drafts\.(remove|delete)|Files\.(remove|delete|emptyTrash)|Labels\.(remove|delete)|setTrashed|trashed:\s*true/);
  // Event cancellation is the one removal, and it goes through Calendar.Events.remove only.
  assert.equal((code.match(/\.remove\(/g) || []).length, 1);
});

test('checkSetup reports status without logging the secret', () => {
  const gas = createGas();
  const report = gas.ctx.checkSetup();
  assert.equal(report.ready, true);
  assert.match(report.secret, /^set \(\d+ characters\)$/);
  assert.ok(gas.state.logs.length > 0);
  assert.ok(gas.state.logs.every((l) => !l.includes(SECRET)));
  const short = createGas({ props: { BRIDGE_SHARED_SECRET: 'short' } }).ctx.checkSetup();
  assert.equal(short.ready, false);
  assert.match(short.secret, /too short/);
});

test('unexpected Google errors map to bridge error codes', () => {
  const gas = createGas();
  assert.equal(gas.post('read_email', { message_id: 'missing' }).error.code, 'not_found');
  gas.ctx.Gmail.Users.Labels.list = () => { throw new Error('API call to gmail.users.labels.list failed with error: Insufficient Permission'); };
  assert.equal(gas.post('list_labels', {}).error.code, 'permission_denied');
  gas.ctx.Gmail.Users.Labels.list = () => { throw new Error('Service invoked too many times for one day: gmail.'); };
  assert.equal(gas.post('list_labels', {}).error.code, 'quota');
});
