'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createEnv, formatDate } = require('./helpers/gas');

test('formatDate helper (test fake) matches SimpleDateFormat output', () => {
  const d = new Date('2026-11-05T08:30:00Z');
  assert.equal(formatDate(d, 'Asia/Kolkata', "yyyy-MM-dd'T'HH:mm:ssXXX"), '2026-11-05T14:00:00+05:30');
  assert.equal(formatDate(d, 'Asia/Kolkata', 'EEE d MMM yyyy, HH:mm'), 'Thu 5 Nov 2026, 14:00');
});

// ---------------- Calendar ----------------

test('list_calendar_events formats timed and all-day events in the calendar time zone', () => {
  const env = createEnv();
  const { result } = env.call('list_calendar_events', { start: '2026-11-05T00:00:00+05:30', end: '2026-11-07T00:00:00+05:30', query: 'review' });
  assert.equal(result.time_zone, 'Asia/Kolkata');
  const [e1, e2] = result.events;
  assert.deepEqual([e1.id, e1.title, e1.start, e1.end, e1.all_day], ['e1@google.com', 'Design review', '2026-11-05T14:00:00+05:30', '2026-11-05T15:00:00+05:30', false]);
  assert.deepEqual(e1.attendees, [{ email: 'priya@example.com', name: '', response_status: 'INVITED' }]);
  assert.deepEqual([e2.start, e2.end, e2.all_day], ['2026-11-06', '2026-11-07', true]);
  assert.deepEqual(env.calls[0].args[2], { search: 'review' });
});

test('check_availability returns busy times, ignoring all-day events', () => {
  const env = createEnv();
  const { result } = env.call('check_availability', { start: '2026-11-05T00:00:00+05:30', end: '2026-11-07T00:00:00+05:30' });
  assert.deepEqual(result.busy, [{ start: '2026-11-05T14:00:00+05:30', end: '2026-11-05T15:00:00+05:30' }]);
});

test('create_calendar_event sends invites only when asked and there are guests', () => {
  const env = createEnv();
  const res = env.call('create_calendar_event', {
    title: 'Sync', start: '2026-11-09T10:00:00+05:30', end: '2026-11-09T10:30:00+05:30',
    attendees: ['priya@example.com', 'raj@example.com'], send_updates: 'all', location: 'Room 1',
  });
  assert.equal(res.ok, true);
  const [title, start, end, opts] = env.calls.find((c) => c.name === 'Calendar.createEvent').args;
  assert.equal(title, 'Sync');
  assert.equal(start, '2026-11-09T04:30:00.000Z');
  assert.equal(end, '2026-11-09T05:00:00.000Z');
  assert.deepEqual(opts, { description: '', location: 'Room 1', guests: 'priya@example.com,raj@example.com', sendInvites: true });

  env.call('create_calendar_event', { title: 'Solo', start: '2026-11-09T11:00:00+05:30', end: '2026-11-09T11:30:00+05:30', send_updates: 'all' });
  assert.equal(env.calls.filter((c) => c.name === 'Calendar.createEvent')[1].args[3].sendInvites, false);
});

test('moving an event updates it and emails attendees when asked', () => {
  const env = createEnv();
  const res = env.call('update_calendar_event', {
    event_id: 'e1@google.com', start: '2026-11-05T16:00:00+05:30', end: '2026-11-05T17:00:00+05:30', send_updates: 'all',
  });
  assert.equal(res.ok, true);
  assert.equal(res.result.notified, 1);
  assert.deepEqual(env.calls.find((c) => c.name === 'Event.setTime').args, ['e1@google.com', '2026-11-05T10:30:00.000Z', '2026-11-05T11:30:00.000Z']);
  assert.equal(env.sent.length, 1);
  assert.equal(env.sent[0].to, 'priya@example.com');
  assert.equal(env.sent[0].subject, 'Updated: Design review');
  assert.match(env.sent[0].body, /When: Thu 5 Nov 2026, 16:00 - 17:00 \(Asia\/Kolkata\)/);
});

test('updating without notification sends no email', () => {
  const env = createEnv();
  const res = env.call('update_calendar_event', { event_id: 'e1@google.com', title: 'Design sync', send_updates: 'none' });
  assert.equal(res.result.notified, 0);
  assert.equal(env.sent.length, 0);
  assert.equal(env.events.get('e1@google.com').title, 'Design sync');
});

test('attendee list changes add and remove guests', () => {
  const env = createEnv();
  env.call('update_calendar_event', { event_id: 'e1@google.com', attendees: ['raj@example.com'] });
  assert.deepEqual(env.events.get('e1@google.com').guests, ['raj@example.com']);
  assert.equal(env.call('update_calendar_event', { event_id: 'e1@google.com' }).error.code, 'invalid_arguments');
});

test('cancelling an event emails a cancellation when asked', () => {
  const env = createEnv();
  const res = env.call('delete_calendar_event', { event_id: 'e1@google.com', send_updates: 'all' });
  assert.deepEqual(res.result, { deleted: true, notified: 1 });
  assert.equal(env.sent[0].subject, 'Cancelled: Design review');
  assert.equal(env.call('get_calendar_event', { event_id: 'e1@google.com' }).error.code, 'not_found');

  const quiet = createEnv();
  quiet.call('delete_calendar_event', { event_id: 'e1@google.com', send_updates: 'none' });
  assert.equal(quiet.sent.length, 0);
});

// ---------------- Drive ----------------

test('list_drive_files and get_drive_file', () => {
  const env = createEnv();
  const list = env.call('list_drive_files', { name_contains: "it's" }).result;
  assert.deepEqual(list.map((f) => f.name), ['notes.txt']);
  assert.equal(env.calls[0].args[0], "trashed = false and title contains 'it\\'s'"); // quote escaped
  const f = env.call('get_drive_file', { file_id: 'f1' }).result;
  assert.deepEqual([f.mime_type, f.size, f.trashed], ['text/plain', 12, false]);
  assert.equal(env.call('get_drive_file', { file_id: 'zzz' }).error.code, 'not_found');
});

test('save_attachments_to_drive saves to "Email Attachments" and skips > 25 MB', () => {
  const env = createEnv();
  const { result } = env.call('save_attachments_to_drive', { message_id: 'm3' });
  assert.equal(result.folder.name, 'Email Attachments');
  assert.deepEqual(result.saved.map((s) => s.name), ['report.pdf']);
  assert.match(result.saved[0].url, /^https:\/\/drive\.google\.com\/file\/d\//); // FR-30
  assert.deepEqual(result.skipped, [{ name: 'huge.mov', size: 31457280, reason: 'larger than 25 MB' }]); // AT-23

  // Second save reuses the folder and honours the name filter (case-insensitive).
  const again = env.call('save_attachments_to_drive', { message_id: 'm3', attachment_names: ['REPORT.PDF'] }).result;
  assert.equal(again.saved.length, 1);
  assert.equal(env.calls.filter((c) => c.name === 'DriveApp.createFolder').length, 1);
  assert.equal(env.folders[0]._files.length, 2);
});

test('trash_drive_file trashes, never deletes', () => {
  const env = createEnv();
  assert.equal(env.call('trash_drive_file', { file_id: 'f1' }).ok, true);
  assert.equal(env.files.get('f1').isTrashed(), true);
  assert.deepEqual(env.calls.find((c) => c.name === 'File.setTrashed').args, ['f1', true]);
});
