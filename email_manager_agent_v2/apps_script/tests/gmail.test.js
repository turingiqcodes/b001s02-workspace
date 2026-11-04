'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createEnv } = require('./helpers/gas');

test('get_latest_emails returns the latest message of each inbox conversation', () => {
  const env = createEnv();
  const res = env.call('get_latest_emails', { max_results: 2 });
  assert.equal(res.ok, true);
  const [t1, t3] = res.result;
  assert.equal(t1.id, 'm2'); // latest message in conversation t1
  assert.equal(t1.thread_message_count, 2);
  assert.equal(t1.body, 'Sure thing'); // HTML stripped
  assert.equal(t3.id, 'm3');
  assert.deepEqual(env.calls[0], { name: 'GmailApp.getInboxThreads', args: [0, 2] });
});

test('read_email parses headers, recipients, labels and attachments', () => {
  const env = createEnv();
  const m = env.call('read_email', { message_id: 'm1' }).result;
  assert.equal(m.subject, 'Meeting');
  assert.equal(m.from, 'Priya <priya@example.com>');
  assert.deepEqual(m.to, ['me@example.com', '"Doe, Jane" <jane@example.com>']); // quoted comma kept
  assert.equal(m.body, 'Hello from Priya');
  assert.equal(m.unread, true);
  assert.deepEqual(m.labels, ['INBOX', 'UNREAD']);
  assert.equal(m.message_id_header, '<abc@mail>');
  assert.equal(m.date, '2026-11-03T10:00:00.000Z');
  const files = env.call('read_email', { message_id: 'm3' }).result.attachments;
  assert.deepEqual(files.map((a) => [a.name, a.size, a.mime_type]), [
    ['report.pdf', 2097152, 'application/pdf'],
    ['huge.mov', 31457280, 'video/quicktime'],
  ]);
});

test('search_emails passes the Gmail query through', () => {
  const env = createEnv();
  assert.equal(env.call('search_emails', { query: 'from:priya has:attachment', max_results: 5 }).ok, true);
  assert.deepEqual(env.calls[0], { name: 'GmailApp.search', args: ['from:priya has:attachment', 0, 5] });
});

test('read_thread returns every message', () => {
  const env = createEnv();
  const { result } = env.call('read_thread', { thread_id: 't1' });
  assert.equal(result.subject, 'Meeting');
  assert.deepEqual(result.messages.map((m) => m.id), ['m1', 'm2']);
  assert.equal(env.call('read_thread', { thread_id: 'nope' }).error.code, 'not_found');
});

test('reply drafts use createDraftReply so Gmail keeps the thread', () => {
  const env = createEnv();
  const res = env.call('create_draft', {
    reply_to_message_id: 'm1', to: ['priya@example.com'], cc: ['raj@example.com'], subject: 'Re: Meeting', body: 'Thanks!',
    thread_id: 't1', in_reply_to: '<abc@mail>', references: '<abc@mail>',
  });
  assert.equal(res.ok, true);
  assert.deepEqual(env.calls.find((c) => c.name === 'Message.createDraftReply').args, [
    'm1', 'Thanks!', { cc: 'raj@example.com', subject: 'Re: Meeting' },
  ]);
  const draft = env.call('get_draft', { draft_id: res.result.draft_id }).result;
  assert.equal(draft.thread_id, 't1');
  assert.equal(draft.in_reply_to, '<abc@mail>');
});

test('draft create, list, update, send and delete', () => {
  const env = createEnv();
  const created = env.call('create_draft', { to: ['bob@example.com'], cc: ['c@example.com'], bcc: ['d@example.com'], subject: 'Hi', body: 'v1' }).result;
  assert.deepEqual(env.calls.find((c) => c.name === 'GmailApp.createDraft').args, [
    'bob@example.com', 'Hi', 'v1', { cc: 'c@example.com', bcc: 'd@example.com' },
  ]);
  const [d] = env.call('list_drafts').result;
  assert.deepEqual([d.to, d.cc, d.bcc, d.body], [['bob@example.com'], ['c@example.com'], ['d@example.com'], 'v1']);

  env.call('update_draft', { draft_id: created.draft_id, to: ['bob@example.com'], subject: 'Hi', body: 'v2' });
  assert.equal(env.call('get_draft', { draft_id: created.draft_id }).result.body, 'v2');

  assert.equal(env.call('send_draft', { draft_id: created.draft_id }).ok, true);
  assert.equal(env.call('get_draft', { draft_id: created.draft_id }).error.code, 'not_found');

  const second = env.call('create_draft', { subject: 'tmp' }).result;
  assert.deepEqual(env.call('delete_draft', { draft_id: second.draft_id }).result, { deleted: true });
});

test('send_email uses GmailApp.sendEmail and needs a recipient', () => {
  const env = createEnv();
  assert.equal(env.call('send_email', { to: [], subject: 'x', body: 'y' }).error.code, 'invalid_arguments');
  assert.deepEqual(env.call('send_email', { to: ['a@example.com', 'b@example.com'], subject: 'Café ☕', body: 'Hi' }).result, { sent: true });
  assert.deepEqual(env.sent[0], { to: 'a@example.com,b@example.com', subject: 'Café ☕', body: 'Hi', o: {} });
});

test('labels: user labels case-insensitive on the conversation, missing ones created', () => {
  const env = createEnv();
  const res = env.call('update_email_labels', { message_id: 'm1', add_labels: ['WORK', 'Travel'], remove_labels: ['unread'] });
  assert.equal(res.ok, true);
  assert.deepEqual(env.calls.filter((c) => c.name === 'Label.addToThread').map((c) => c.args), [['Work', 't1'], ['Travel', 't1']]);
  assert.ok(env.calls.some((c) => c.name === 'GmailApp.createLabel' && c.args[0] === 'Travel'));
  assert.ok(env.calls.some((c) => c.name === 'Message.markRead'));
  assert.deepEqual(res.result.labels, ['INBOX', 'Work', 'Travel']);
});

test('labels: system labels map to GmailApp actions', () => {
  const env = createEnv();
  env.call('update_email_labels', { message_id: 'm1', add_labels: ['starred', 'important'], remove_labels: ['INBOX'] });
  const names = env.calls.map((c) => c.name);
  assert.ok(names.includes('Message.star'));
  assert.ok(names.includes('Thread.markImportant'));
  assert.ok(names.includes('Thread.moveToArchive'));
});

test('removing an unknown label changes nothing', () => {
  const env = createEnv();
  const res = env.call('update_email_labels', { message_id: 'm1', add_labels: ['Work'], remove_labels: ['nope'] });
  assert.equal(res.error.code, 'not_found');
  assert.ok(!env.calls.some((c) => c.name === 'Label.addToThread'));
});

test('trash one message, restore its conversation', () => {
  const env = createEnv();
  assert.equal(env.call('trash_email', { message_id: 'm1' }).ok, true);
  assert.equal(env.messages.get('m1').trash, true);
  assert.equal(env.messages.get('m2').trash, false); // only that message
  assert.equal(env.call('read_email', { message_id: 'm1' }).result.labels[0], 'TRASH');

  const res = env.call('restore_email', { message_id: 'm1' });
  assert.deepEqual(res.result, { restored: true, thread_message_count: 2 });
  assert.equal(env.messages.get('m1').trash, false);
  assert.equal(env.call('restore_email', { message_id: 'm1' }).error.code, 'invalid_arguments'); // not in Trash
});

test('list_gmail_labels includes system and user labels', () => {
  const env = createEnv();
  const names = env.call('list_gmail_labels').result.map((l) => `${l.type}:${l.name}`);
  assert.deepEqual(names, ['system:INBOX', 'system:UNREAD', 'system:STARRED', 'system:IMPORTANT', 'user:Work']);
});
