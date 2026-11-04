'use strict';
/**
 * Minimal in-memory fakes of the built-in Apps Script services used by Code.gs
 * (GmailApp, CalendarApp, DriveApp, ...), so the bridge can be tested with
 * plain Node - no Google account needed.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CODE = fs.readFileSync(path.join(__dirname, '..', '..', 'Code.gs'), 'utf8');
const SECRET = 'test-secret-0123456789abcdef';
const MB = 1024 * 1024;
const TZ = 'Asia/Kolkata';

// ---------- Utilities.formatDate (subset of SimpleDateFormat) ----------
function formatDate(date, tz, pattern) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit',
      minute: '2-digit', second: '2-digit', weekday: 'short', hourCycle: 'h23',
    }).formatToParts(date).map((p) => [p.type, p.value]),
  );
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
  const offMin = Math.round((asUtc - Math.floor(date.getTime() / 1000) * 1000) / 60000);
  const sign = offMin >= 0 ? '+' : '-';
  const abs = Math.abs(offMin);
  const offset = `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const tokens = {
    yyyy: parts.year, MMM: months[+parts.month - 1], MM: parts.month, dd: parts.day, d: String(+parts.day),
    HH: parts.hour, mm: parts.minute, ss: parts.second, XXX: offset, EEE: parts.weekday,
  };
  return pattern.replace(/'([^']*)'|yyyy|MMM|MM|dd|d|HH|mm|ss|XXX|EEE/g, (m, lit) => (lit !== undefined ? lit : tokens[m]));
}

function createEnv(opts = {}) {
  const calls = [];
  // JSON round-trip copies values into this realm so deepStrictEqual works.
  const rec = (name, ...args) => calls.push({ name, args: JSON.parse(JSON.stringify(args)) });
  let seq = 100;

  // ======================= Gmail =======================
  const threads = new Map(); // id -> {id, inbox, important, labels:Set}
  const messages = new Map(); // id -> plain data
  const drafts = new Map(); // draftId -> messageId
  const userLabels = new Map([['work', 'Work']]); // lower -> name

  function addMessage(m) {
    const data = {
      to: '', cc: '', bcc: '', replyTo: '', plain: '', html: '', attachments: [], unread: false,
      starred: false, trash: false, headers: {}, date: new Date('2026-11-03T10:00:00Z'), ...m,
    };
    messages.set(data.id, data);
    if (!threads.has(data.threadId)) {
      threads.set(data.threadId, { id: data.threadId, inbox: true, important: false, labels: new Set() });
    }
    return data;
  }

  addMessage({
    id: 'm1', threadId: 't1', subject: 'Meeting', from: 'Priya <priya@example.com>',
    to: 'me@example.com, "Doe, Jane" <jane@example.com>', plain: 'Hello from Priya', unread: true,
    headers: { 'Message-ID': '<abc@mail>' },
  });
  addMessage({ id: 'm2', threadId: 't1', subject: 'Re: Meeting', from: 'me@example.com', to: 'priya@example.com', html: '<p>Sure&nbsp;thing</p><style>x{}</style>' });
  addMessage({
    id: 'm3', threadId: 't3', subject: 'Files', from: 'bob@example.com', plain: 'see attached',
    attachments: [
      { name: 'report.pdf', size: 2 * MB, type: 'application/pdf' },
      { name: 'huge.mov', size: 30 * MB, type: 'video/quicktime' },
    ],
  });

  const threadMsgs = (tid) => [...messages.values()].filter((m) => m.threadId === tid);

  function labelObj(name) {
    return {
      getName: () => name,
      addToThread: (t) => (rec('Label.addToThread', name, t.getId()), threads.get(t.getId()).labels.add(name)),
      removeFromThread: (t) => (rec('Label.removeFromThread', name, t.getId()), threads.get(t.getId()).labels.delete(name)),
    };
  }

  function threadObj(tid) {
    const t = threads.get(tid);
    return {
      getId: () => tid,
      getMessages: () => threadMsgs(tid).map((m) => msgObj(m.id)),
      getMessageCount: () => threadMsgs(tid).length,
      getFirstMessageSubject: () => threadMsgs(tid)[0].subject,
      isInInbox: () => t.inbox,
      isImportant: () => t.important,
      getLabels: () => [...t.labels].map(labelObj),
      moveToInbox: () => { rec('Thread.moveToInbox', tid); t.inbox = true; threadMsgs(tid).forEach((m) => { m.trash = false; }); },
      moveToArchive: () => { rec('Thread.moveToArchive', tid); t.inbox = false; },
      markImportant: () => { rec('Thread.markImportant', tid); t.important = true; },
      markUnimportant: () => { rec('Thread.markUnimportant', tid); t.important = false; },
    };
  }

  function attachmentObj(a) {
    return {
      getName: () => a.name,
      getSize: () => a.size,
      getContentType: () => a.type,
      copyBlob: () => ({ name: a.name, mime: a.type, size: a.size }),
    };
  }

  function msgObj(id) {
    const m = messages.get(id);
    return {
      getId: () => id,
      getThread: () => threadObj(m.threadId),
      getSubject: () => m.subject,
      getFrom: () => m.from,
      getTo: () => m.to,
      getCc: () => m.cc,
      getBcc: () => m.bcc,
      getReplyTo: () => m.replyTo,
      getDate: () => m.date,
      getPlainBody: () => m.plain,
      getBody: () => m.html || m.plain,
      getHeader: (h) => m.headers[h] || '',
      getAttachments: () => m.attachments.map(attachmentObj),
      isUnread: () => m.unread,
      isStarred: () => m.starred,
      isInTrash: () => m.trash,
      markRead: () => { rec('Message.markRead', id); m.unread = false; },
      markUnread: () => { rec('Message.markUnread', id); m.unread = true; },
      star: () => { rec('Message.star', id); m.starred = true; },
      unstar: () => { rec('Message.unstar', id); m.starred = false; },
      moveToTrash: () => { rec('Message.moveToTrash', id); m.trash = true; },
      createDraftReply: (body, o) => {
        rec('Message.createDraftReply', id, body, o);
        const subject = o && o.subject ? o.subject : 'Re: ' + m.subject;
        return newDraft({ threadId: m.threadId, to: m.replyTo || m.from, cc: (o && o.cc) || '', bcc: (o && o.bcc) || '', subject, plain: body,
          headers: { 'In-Reply-To': m.headers['Message-ID'] || '', References: m.headers['Message-ID'] || '' } });
      },
    };
  }

  function newDraft(fields) {
    const did = 'r' + ++seq;
    const mid = 'dm' + seq;
    addMessage({ id: mid, threadId: fields.threadId || 'td' + seq, from: 'me@example.com', ...fields });
    threads.get(messages.get(mid).threadId).inbox = false;
    drafts.set(did, mid);
    return draftObj(did);
  }

  function draftObj(did) {
    return {
      getId: () => did,
      getMessageId: () => drafts.get(did),
      getMessage: () => msgObj(drafts.get(did)),
      update: (to, subject, body, o) => {
        rec('Draft.update', did, to, subject, body, o);
        Object.assign(messages.get(drafts.get(did)), { to, subject, plain: body, cc: (o && o.cc) || '', bcc: (o && o.bcc) || '' });
        return draftObj(did);
      },
      deleteDraft: () => { rec('Draft.deleteDraft', did); messages.delete(drafts.get(did)); drafts.delete(did); },
      send: () => { rec('Draft.send', did); const mid = drafts.get(did); drafts.delete(did); return msgObj(mid); },
    };
  }

  const sent = [];
  const GmailApp = {
    getInboxThreads: (start, max) => {
      rec('GmailApp.getInboxThreads', start, max);
      return [...threads.values()].filter((t) => t.inbox && threadMsgs(t.id).some((m) => !m.trash)).slice(start, start + max).map((t) => threadObj(t.id));
    },
    search: (q, start, max) => {
      rec('GmailApp.search', q, start, max);
      return [...threads.values()].filter((t) => threadMsgs(t.id).some((m) => !m.trash)).slice(start, start + max).map((t) => threadObj(t.id));
    },
    getMessageById: (id) => (rec('GmailApp.getMessageById', id), messages.has(id) ? msgObj(id) : null),
    getThreadById: (id) => (rec('GmailApp.getThreadById', id), threads.has(id) ? threadObj(id) : null),
    getDrafts: () => [...drafts.keys()].map(draftObj),
    getDraft: (id) => (drafts.has(id) ? draftObj(id) : null),
    createDraft: (to, subject, body, o) => {
      rec('GmailApp.createDraft', to, subject, body, o);
      return newDraft({ to, subject, plain: body, cc: (o && o.cc) || '', bcc: (o && o.bcc) || '' });
    },
    getUserLabels: () => [...userLabels.values()].map(labelObj),
    createLabel: (name) => { rec('GmailApp.createLabel', name); userLabels.set(name.toLowerCase(), name); return labelObj(name); },
    sendEmail: (to, subject, body, o) => { rec('GmailApp.sendEmail', to, subject, body, o); sent.push(JSON.parse(JSON.stringify({ to, subject, body, o: o || {} }))); },
  };

  // ======================= Calendar =======================
  const events = new Map();
  function addEvent(e) {
    events.set(e.id, { description: '', location: '', guests: [], allDay: false, ...e });
  }
  addEvent({ id: 'e1@google.com', title: 'Design review', start: new Date('2026-11-05T08:30:00Z'), end: new Date('2026-11-05T09:30:00Z'), guests: ['priya@example.com'] });
  addEvent({ id: 'e2@google.com', title: 'Holiday', allDay: true, start: new Date('2026-11-05T18:30:00Z'), end: new Date('2026-11-06T18:30:00Z') });

  function eventObj(id) {
    const e = events.get(id);
    return {
      getId: () => id,
      getTitle: () => e.title,
      getDescription: () => e.description,
      getLocation: () => e.location,
      isAllDayEvent: () => e.allDay,
      getStartTime: () => e.start,
      getEndTime: () => e.end,
      getAllDayStartDate: () => e.start,
      getAllDayEndDate: () => e.end,
      getGuestList: () => e.guests.map((g) => ({ getEmail: () => g, getName: () => '', getGuestStatus: () => 'INVITED' })),
      getCreators: () => ['me@example.com'],
      setTitle: (v) => { rec('Event.setTitle', id, v); e.title = v; },
      setDescription: (v) => { rec('Event.setDescription', id, v); e.description = v; },
      setLocation: (v) => { rec('Event.setLocation', id, v); e.location = v; },
      setTime: (s, en) => { rec('Event.setTime', id, s, en); e.start = s; e.end = en; },
      addGuest: (g) => { rec('Event.addGuest', id, g); e.guests.push(g); },
      removeGuest: (g) => { rec('Event.removeGuest', id, g); e.guests = e.guests.filter((x) => x !== g); },
      deleteEvent: () => { rec('Event.deleteEvent', id); events.delete(id); },
    };
  }

  const calendar = {
    getTimeZone: () => TZ,
    getEvents: (start, end, o) => {
      rec('Calendar.getEvents', start, end, o);
      return [...events.values()].filter((e) => e.start < end && start < e.end).map((e) => eventObj(e.id));
    },
    getEventById: (id) => (events.has(id) ? eventObj(id) : null),
    createEvent: (title, start, end, o) => {
      rec('Calendar.createEvent', title, start, end, o);
      const id = 'ev' + ++seq + '@google.com';
      addEvent({ id, title, start, end, description: o.description, location: o.location, guests: o.guests ? o.guests.split(',') : [] });
      return eventObj(id);
    },
  };
  const CalendarApp = { getDefaultCalendar: () => calendar };

  // ======================= Drive =======================
  const files = new Map();
  const folders = [];
  const makeFile = (id, name, mime, size) => {
    const f = {
      _trashed: false,
      getId: () => id,
      getName: () => name,
      getMimeType: () => mime,
      getSize: () => size,
      getUrl: () => 'https://drive.google.com/file/d/' + id,
      getDateCreated: () => new Date('2026-10-01T00:00:00Z'),
      getLastUpdated: () => new Date('2026-10-02T00:00:00Z'),
      isTrashed: () => f._trashed,
      setTrashed: (v) => { rec('File.setTrashed', id, v); f._trashed = v; return f; },
    };
    files.set(id, f);
    return f;
  };
  makeFile('f1', 'notes.txt', 'text/plain', 12);
  const iter = (arr) => { let i = 0; return { hasNext: () => i < arr.length, next: () => arr[i++] }; };
  const makeFolder = (name) => {
    const folder = {
      _files: [],
      getId: () => 'folder_' + name,
      getName: () => name,
      getUrl: () => 'https://drive.google.com/drive/folders/' + name,
      isTrashed: () => false,
      createFile: (blob) => {
        rec('Folder.createFile', name, blob.name, blob.mime);
        const f = makeFile('nf' + ++seq, blob.name, blob.mime, blob.size);
        folder._files.push(f);
        return f;
      },
    };
    folders.push(folder);
    return folder;
  };
  const DriveApp = {
    searchFiles: (q) => (rec('DriveApp.searchFiles', q), iter([...files.values()].filter((f) => !f.isTrashed()))),
    getFileById: (id) => {
      rec('DriveApp.getFileById', id);
      if (!files.has(id)) throw new Error('No item with the given ID could be found.');
      return files.get(id);
    },
    getFoldersByName: (n) => (rec('DriveApp.getFoldersByName', n), iter(folders.filter((f) => f.getName() === n))),
    createFolder: (n) => (rec('DriveApp.createFolder', n), makeFolder(n)),
  };

  // ======================= misc =======================
  const Utilities = { formatDate };
  const ContentService = {
    MimeType: { JSON: 'application/json' },
    createTextOutput: (content) => ({ content, setMimeType() { return this; } }),
  };
  const props = { BRIDGE_SECRET: opts.secret === undefined ? SECRET : opts.secret };
  const PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => props[k] ?? null }) };
  const Session = { getEffectiveUser: () => ({ getEmail: () => 'me@example.com' }) };

  const context = vm.createContext({ GmailApp, CalendarApp, DriveApp, Utilities, ContentService, PropertiesService, Session, console });
  vm.runInContext(CODE, context, { filename: 'Code.gs' });

  function post(body) {
    const raw = typeof body === 'string' ? body : JSON.stringify(body);
    return JSON.parse(context.doPost({ postData: { contents: raw } }).content);
  }

  function call(operation, args = {}, secret = SECRET) {
    return post({ secret, operation, args });
  }

  const googleCalls = () => calls.filter((c) => !/^(GmailApp\.getMessageById|GmailApp\.getThreadById)$/.test(c.name));

  return { context, call, post, calls, googleCalls, messages, threads, drafts, events, files, folders, userLabels, sent };
}

module.exports = { createEnv, SECRET, MB, formatDate };
