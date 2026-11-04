'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createGas } = require('./helpers/gas');

function seed(gas) {
  gas.state.events.ev1 = {
    id: 'ev1', summary: 'Standup', status: 'confirmed', organizer: { self: true },
    start: { dateTime: '2025-01-06T09:00:00+05:30' }, end: { dateTime: '2025-01-06T09:30:00+05:30' },
    attendees: [{ email: 'me@example.com', self: true, responseStatus: 'accepted' }, { email: 'bob@example.com' }],
  };
  gas.state.events.ev2 = { id: 'ev2', summary: 'Old', status: 'cancelled', start: { date: '2025-01-06' }, end: { date: '2025-01-07' } };
}

test('list_events returns normalised events and skips cancelled ones', () => {
  const gas = createGas();
  seed(gas);
  const data = gas.ok('list_events', { start: '2025-01-06T00:00:00+05:30', end: '2025-01-07T00:00:00+05:30', query: 'standup' });
  const args = gas.calls('Events.list').pop().args;
  assert.deepEqual([args.singleEvents, args.orderBy, args.q, args.maxResults], [true, 'startTime', 'standup', 25]);
  assert.equal(data.events.length, 1);
  const ev = data.events[0];
  assert.deepEqual(
    [ev.id, ev.title, ev.all_day, ev.organizer_self, ev.self_response, ev.transparency, ev.attendees],
    ['ev1', 'Standup', false, true, 'accepted', 'opaque', ['me@example.com', 'bob@example.com']]);
});

test('create_event only emails invitations when there are attendees', () => {
  const gas = createGas();
  const ev = gas.ok('create_event', { title: 'Planning', start: '2025-01-07T10:00:00+05:30', end: '2025-01-07T10:45:00+05:30', attendees: ['bob@example.com'] });
  assert.equal(ev.title, 'Planning');
  assert.equal(gas.calls('Events.insert')[0].args.args.sendUpdates, 'all');
  gas.ok('create_event', { title: 'Focus', start: '2025-01-07T11:00:00Z', end: '2025-01-07T12:00:00Z' });
  assert.equal(gas.calls('Events.insert')[1].args.args.sendUpdates, 'none');
  assert.match(gas.post('create_event', { title: 'x', start: '2025-01-07T11:00:00Z', end: '2025-01-07T10:00:00Z' }).error.message, /after the start/);
});

test('move_event patches start and end and notifies attendees', () => {
  const gas = createGas();
  seed(gas);
  const ev = gas.ok('move_event', { event_id: 'ev1', new_start: '2025-01-06T16:00:00+05:30', new_end: '2025-01-06T16:30:00+05:30' });
  assert.equal(ev.start, '2025-01-06T16:00:00+05:30');
  const call = gas.calls('Events.patch')[0].args;
  assert.deepEqual(call.res, { start: { dateTime: '2025-01-06T16:00:00+05:30' }, end: { dateTime: '2025-01-06T16:30:00+05:30' } });
  assert.equal(call.args.sendUpdates, 'all');
  assert.match(gas.post('move_event', { event_id: 'ev1', new_start: '2025-01-06T16:00:00Z' }).error.message, /new_end' is required/);
});

test('cancel_event checks the event exists and respects notify_attendees', () => {
  const gas = createGas();
  seed(gas);
  assert.equal(gas.post('cancel_event', { event_id: 'missing' }).error.code, 'not_found');
  assert.equal(gas.calls('Events.remove').length, 0);
  assert.deepEqual(gas.ok('cancel_event', { event_id: 'ev1', notify_attendees: false }), { cancelled: true, event_id: 'ev1' });
  assert.equal(gas.calls('Events.remove')[0].args.args.sendUpdates, 'none');
});
