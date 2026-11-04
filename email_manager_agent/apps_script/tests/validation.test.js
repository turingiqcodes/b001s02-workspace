'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createGas } = require('./helpers/gas');

function errorFor(action, params) {
  const r = createGas().post(action, params);
  assert.equal(r.ok, false, action + ' should fail for ' + JSON.stringify(params));
  return r.error;
}

test('every action re-validates its parameters', () => {
  const cases = [
    ['search_emails', { query: 'x', extra: 1 }, /not a known parameter/],
    ['search_emails', { max_results: 26 }, /between 1 and 25/],
    ['search_emails', { max_results: 2.5 }, /whole number/],
    ['read_email', {}, /required/],
    ['read_email', { message_id: 'bad id with spaces' }, /invalid format/],
    ['send_email', { to: ['not-an-email'], subject: 's', body: 'b' }, /invalid email/],
    ['send_email', { to: [], subject: 's', body: 'b' }, /at least one/],
    ['send_email', { to: 'a@example.com', subject: 's', body: 'b' }, /list of email/],
    ['reply_to_email', { message_id: 'm1', body: 'b', reply_all: 'yes' }, /true or false/],
    ['list_events', { start: '2025-01-06T09:00:00', end: '2025-01-06T10:00:00Z' }, /timezone offset/],
    ['list_events', { start: '2025-01-06T10:00:00Z', end: '2025-01-06T09:00:00Z' }, /end time must be after/],
    ['list_events', { start: '2025-01-01T00:00:00Z', end: '2025-04-01T00:00:00Z' }, /at most 62 days/],
    ['search_drive_files', { mime_type: 'video' }, /must be one of/],
    ['search_drive_files', { modified_after: '2025/01/01' }, /date like/],
    ['modify_labels', { message_ids: [], add_labels: ['Receipts'] }, /at least 1/],
    ['modify_labels', { message_ids: Array.from({ length: 101 }, (_, i) => 'm' + i), add_labels: ['Receipts'] }, /at most 100/],
    ['create_label', { name: '   ' }, /must not be empty/],
  ];
  for (const [action, params, re] of cases) {
    const err = errorFor(action, params);
    assert.equal(err.code, 'invalid_params', action);
    assert.match(err.message, re, action + ': ' + err.message);
  }
});

test('limits total recipients and body size', () => {
  const to = Array.from({ length: 15 }, (_, i) => 'p' + i + '@example.com');
  const cc = Array.from({ length: 6 }, (_, i) => 'c' + i + '@example.com');
  assert.match(errorFor('send_email', { to, cc, subject: 's', body: 'b' }).message, /At most 20 recipients/);
  assert.match(errorFor('send_email', { to: ['a@example.com'], subject: 's', body: 'x'.repeat(100001) }).message, /too long/);
});

test('params must be an object', () => {
  assert.equal(createGas().post('list_labels', ['x']).error.code, 'invalid_params');
});
