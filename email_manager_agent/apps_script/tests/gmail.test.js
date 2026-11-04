'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createGas, decodeRaw } = require('./helpers/gas');

test('search_emails defaults to the inbox and returns summaries', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', subject: 'Invoice', labelIds: ['INBOX', 'UNREAD', 'Label_1'], attachments: [{ filename: 'a.pdf', size: 10 }] });
  gas.addMessage({ id: 'm2', subject: 'Hi', labelIds: ['INBOX'] });
  const data = gas.ok('search_emails', {});
  assert.equal(gas.calls('Messages.list')[0].args.q, 'in:inbox');
  assert.equal(gas.calls('Messages.list')[0].args.maxResults, 10);
  assert.equal(data.result_size_estimate, 2);
  const [m1, m2] = data.messages;
  assert.deepEqual(
    { id: m1.id, thread_id: m1.thread_id, subject: m1.subject, unread: m1.unread, has_attachments: m1.has_attachments, labels: m1.labels },
    { id: 'm1', thread_id: 'thread_m1', subject: 'Invoice', unread: true, has_attachments: true, labels: ['INBOX', 'UNREAD', 'Receipts'] });
  assert.equal(m2.unread, false);
  assert.equal(m1.date, '2025-01-06T10:00:00.000Z');
});

test('read_email returns the text body and attachment list', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', body: 'Please pay — €120', cc: 'bob@example.com', attachments: [{ filename: 'invoice.pdf', size: 2048 }] });
  const data = gas.ok('read_email', { message_id: 'm1' });
  assert.equal(data.body, 'Please pay — €120');
  assert.equal(data.cc, 'bob@example.com');
  assert.deepEqual(data.attachments, [{ attachment_id: '1', filename: 'invoice.pdf', mime_type: 'application/pdf', size: 2048 }]);
});

test('send_email builds base64url MIME with encoded non-ASCII headers', () => {
  const gas = createGas();
  const data = gas.ok('send_email', { to: ['a@example.com'], cc: ['b@example.com'], bcc: ['c@example.com'], subject: 'Café plans ☕', body: 'See you at 5 — ok?' });
  assert.match(data.message_id, /^sent_/);
  const raw = gas.state.sent[0].raw;
  assert.doesNotMatch(raw, /[+/]/, 'raw must be base64url (Gmail accepts the = padding Apps Script adds)');
  const msg = decodeRaw(raw);
  assert.equal(msg.headers.to, 'a@example.com');
  assert.equal(msg.headers.cc, 'b@example.com');
  assert.equal(msg.headers.bcc, 'c@example.com');
  assert.match(msg.headers.subject, /^=\?UTF-8\?B\?.+\?=$/);
  assert.equal(Buffer.from(msg.headers.subject.slice(10, -2), 'base64').toString('utf8'), 'Café plans ☕');
  assert.equal(msg.body, 'See you at 5 — ok?');
});

test('header injection through the subject is neutralised', () => {
  const gas = createGas();
  gas.ok('send_email', { to: ['a@example.com'], subject: 'Hi\r\nBcc: evil@example.com', body: 'x' });
  const msg = decodeRaw(gas.state.sent[0].raw);
  assert.equal(msg.headers.bcc, undefined);
  assert.equal(msg.headers.subject, 'Hi Bcc: evil@example.com');
});

test('reply_to_email threads the reply and reply_all excludes me', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', from: 'Alice <alice@example.com>', to: 'me@example.com, Carol <carol@example.com>', cc: 'dave@example.com', subject: 'Report' });
  const one = gas.ok('reply_to_email', { message_id: 'm1', body: 'Thanks' });
  assert.deepEqual([one.to, one.cc], [['alice@example.com'], []]);
  let sent = gas.state.sent[0];
  assert.equal(sent.threadId, 'thread_m1');
  let msg = decodeRaw(sent.raw);
  assert.equal(msg.headers.subject, 'Re: Report');
  assert.equal(msg.headers['in-reply-to'], '<m1@mail.example.com>');
  assert.equal(msg.headers.references, '<m1@mail.example.com>');

  const all = gas.ok('reply_to_email', { message_id: 'm1', body: 'Thanks all', reply_all: true });
  assert.deepEqual(all.cc, ['carol@example.com', 'dave@example.com']);
  msg = decodeRaw(gas.state.sent[1].raw);
  assert.doesNotMatch(msg.headers.cc, /me@example.com/);
});

test('forward_email includes the original message and attachments, skipping oversized ones', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', subject: 'Invoice', body: 'Original text', attachments: [
    { filename: 'small.pdf', size: 1000 }, { filename: 'huge.zip', size: 25 * 1024 * 1024, mimeType: 'application/zip' }] });
  const data = gas.ok('forward_email', { message_id: 'm1', to: ['bob@example.com'], note: 'FYI' });
  assert.deepEqual(data.skipped_attachments.map((s) => s.filename), ['huge.zip']);
  const msg = decodeRaw(gas.state.sent[0].raw);
  assert.equal(msg.headers.subject, 'Fwd: Invoice');
  assert.match(msg.headers['content-type'], /^multipart\/mixed; boundary="/);
  assert.match(msg.body, /^FYI\n\n---------- Forwarded message ---------/);
  assert.match(msg.body, /Original text$/);
  assert.equal(msg.parts.length, 2);
  assert.match(msg.parts[1].headers, /filename="small\.pdf"/);
  assert.equal(msg.parts[1].text, 'file-bytes-att_m1_1');
});

test('drafts can be created as replies, updated and sent', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', subject: 'Plan' });
  const d = gas.ok('create_draft', { to: ['a@example.com'], subject: 'Re: Plan', body: 'Draft', reply_to_message_id: 'm1' });
  assert.match(d.draft_id, /^draft_/);
  assert.equal(gas.calls('Drafts.create')[0].args.message.threadId, 'thread_m1');
  assert.equal(gas.post('update_draft', { draft_id: d.draft_id }).error.message, 'Nothing to change in the draft.');
  gas.ok('update_draft', { draft_id: d.draft_id, body: 'New body' });
  assert.equal(gas.calls('Drafts.update').length, 1);
  assert.match(gas.ok('send_draft', { draft_id: d.draft_id }).message_id, /^sent_/);
  assert.equal(gas.post('send_draft', { draft_id: 'nope' }).error.code, 'not_found');
});

test('labels: create rejects duplicates; modify allows only user and a few system labels', () => {
  const gas = createGas();
  assert.deepEqual(Object.keys(gas.ok('create_label', { name: 'Projects' })), ['id', 'name']);
  assert.match(gas.post('create_label', { name: 'receipts' }).error.message, /already exists/);
  assert.match(gas.post('create_label', { name: 'inbox' }).error.message, /already exists/);

  const r = gas.ok('modify_labels', { message_ids: ['m1', 'm2'], add_labels: ['receipts', 'STARRED'], remove_labels: ['INBOX'] });
  assert.equal(r.modified, 2);
  assert.deepEqual(gas.calls('Messages.batchModify')[0].args, { ids: ['m1', 'm2'], addLabelIds: ['Label_1', 'STARRED'], removeLabelIds: ['INBOX'] });

  for (const label of ['TRASH', 'SPAM', 'SENT']) {
    const e = gas.post('modify_labels', { message_ids: ['m1'], add_labels: [label] }).error;
    assert.equal(e.code, 'invalid_params');
    assert.match(e.message, /system label/);
  }
  assert.equal(gas.post('modify_labels', { message_ids: ['m1'], add_labels: ['Nope'] }).error.code, 'not_found');
  assert.match(gas.post('modify_labels', { message_ids: ['m1'] }).error.message, /No labels/);
  assert.equal(gas.calls('Messages.batchModify').length, 1);
});
