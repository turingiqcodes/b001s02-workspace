/**
 * Email Manager Agent - Google Apps Script bridge.
 *
 * Uses only the built-in Apps Script services: GmailApp, CalendarApp, DriveApp.
 * Nothing needs to be added under "Services" in the editor.
 *
 * Deploy as a Web app: Execute as "Me", Who has access "Anyone".
 * Set Script Property BRIDGE_SECRET (16+ chars) to the same value as
 * APPS_SCRIPT_SECRET in backend/.env.
 *
 * Request:  POST {"secret": "...", "operation": "<name>", "args": {...}}
 * Response: {"ok": true, "result": ...} | {"ok": false, "error": {"code", "message"}}
 *
 * Notes on built-in service behaviour:
 *  - Gmail labels, archive/inbox and restore-from-Trash work on the whole
 *    conversation (GmailApp has no per-message label or untrash methods).
 *  - CalendarApp can't send Google's own "updated"/"cancelled" emails, so when
 *    send_updates is "all" this bridge emails the attendees itself.
 *  - Nothing here ever deletes mail or files permanently (NFR-06).
 */

var MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024; // FR-31
var MAX_BODY_CHARS = 20000; // backend truncates further (NFR-10)
var MAX_COUNT = 5000; // count_emails stops paging here
var DEFAULT_FOLDER = 'Email Attachments';
// Gmail system labels this app can add/remove (anything else is a user label).
var SYSTEM_LABELS = ['INBOX', 'UNREAD', 'STARRED', 'IMPORTANT'];

// ---------- entry points ----------

function doPost(e) {
  var response;
  try {
    var payload;
    try {
      payload = JSON.parse((e && e.postData && e.postData.contents) || '');
    } catch (err) {
      throw bridgeError_('invalid_request', 'Request body must be JSON.');
    }
    response = { ok: true, result: handleRequest_(payload) };
  } catch (err) {
    response = { ok: false, error: toErrorObject_(err) };
  }
  return ContentService.createTextOutput(JSON.stringify(response)).setMimeType(ContentService.MimeType.JSON);
}

function doGet() {
  return ContentService.createTextOutput(JSON.stringify({ ok: true, service: 'email-manager-bridge' }))
    .setMimeType(ContentService.MimeType.JSON);
}

function handleRequest_(payload) {
  if (!payload || typeof payload !== 'object') throw bridgeError_('invalid_request', 'Request must be a JSON object.');
  checkSecret_(payload.secret); // NFR-02
  var op = payload.operation;
  if (typeof op !== 'string' || !Object.prototype.hasOwnProperty.call(OPERATIONS, op)) {
    throw bridgeError_('unknown_operation', 'Operation is not allowed: ' + String(op).slice(0, 60)); // NFR-03
  }
  var args = payload.args == null ? {} : payload.args;
  if (typeof args !== 'object' || Array.isArray(args)) throw bridgeError_('invalid_arguments', 'args must be an object.');
  return OPERATIONS[op](args);
}

// ---------- security ----------

function checkSecret_(provided) {
  var expected = PropertiesService.getScriptProperties().getProperty('BRIDGE_SECRET');
  if (!expected || expected.length < 16) {
    throw bridgeError_('bridge_not_configured', 'BRIDGE_SECRET script property is missing or shorter than 16 characters.');
  }
  if (typeof provided !== 'string' || !constantTimeEquals_(provided, expected)) {
    throw bridgeError_('unauthorized', 'Invalid or missing secret.');
  }
}

function constantTimeEquals_(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---------- errors ----------

function bridgeError_(code, message) {
  var err = new Error(message);
  err.bridgeCode = code;
  return err;
}

function notFound_(what) {
  return bridgeError_('not_found', what + ' was not found.');
}

function toErrorObject_(err) {
  if (err && err.bridgeCode) return { code: err.bridgeCode, message: err.message };
  var msg = String((err && err.message) || err || 'Unknown error');
  if (/not found|invalid argument: id|does not exist|No item with the given ID/i.test(msg)) {
    return { code: 'not_found', message: 'The requested item was not found.' };
  }
  if (/limit|quota|too many times/i.test(msg)) {
    return { code: 'quota_exceeded', message: 'A Google service limit was reached. Try again later.' };
  }
  return { code: 'google_error', message: 'Google service error: ' + msg.slice(0, 300) };
}

// ---------- argument validation (NFR-07) ----------

function reqString_(args, name, maxLen) {
  var v = args[name];
  if (typeof v !== 'string' || !v.trim()) throw bridgeError_('invalid_arguments', name + ' is required.');
  if (v.length > (maxLen || 500)) throw bridgeError_('invalid_arguments', name + ' is too long.');
  return v;
}

function optString_(args, name, maxLen) {
  var v = args[name];
  if (v == null) return '';
  if (typeof v !== 'string') throw bridgeError_('invalid_arguments', name + ' must be a string.');
  if (v.length > (maxLen || 50000)) throw bridgeError_('invalid_arguments', name + ' is too long.');
  return v;
}

function headerString_(args, name, maxLen) {
  var v = optString_(args, name, maxLen || 1000);
  if (/[\r\n]/.test(v)) throw bridgeError_('invalid_arguments', name + ' must not contain line breaks.');
  return v;
}

function optInt_(args, name, def, min, max) {
  var v = args[name];
  if (v == null) return def;
  if (typeof v !== 'number' || Math.floor(v) !== v || v < min || v > max) {
    throw bridgeError_('invalid_arguments', name + ' must be an integer between ' + min + ' and ' + max + '.');
  }
  return v;
}

var EMAIL_RE_ = /^[^@\s<>,;"]+@[^@\s<>,;"]+\.[^@\s<>,;"]+$/;

function emailList_(args, name) {
  var v = args[name];
  if (v == null) return [];
  if (!Array.isArray(v) || v.length > 100) throw bridgeError_('invalid_arguments', name + ' must be a list of email addresses.');
  return v.map(function (x) {
    if (typeof x !== 'string' || !EMAIL_RE_.test(x.trim())) {
      throw bridgeError_('invalid_arguments', name + ' contains an invalid email address.');
    }
    return x.trim();
  });
}

function stringList_(args, name) {
  var v = args[name];
  if (v == null) return [];
  if (!Array.isArray(v) || v.length > 50) throw bridgeError_('invalid_arguments', name + ' must be a list.');
  return v.map(function (x) {
    if (typeof x !== 'string' || !x.trim() || x.length > 255) throw bridgeError_('invalid_arguments', name + ' has an invalid entry.');
    return x.trim();
  });
}

function dateArg_(args, name, required) {
  var v = args[name];
  if (v == null || v === '') {
    if (required) throw bridgeError_('invalid_arguments', name + ' is required.');
    return null;
  }
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.test(v)) {
    throw bridgeError_('invalid_arguments', name + ' must be an ISO 8601 date-time with a UTC offset.');
  }
  var d = new Date(v);
  if (isNaN(d.getTime())) throw bridgeError_('invalid_arguments', name + ' is not a valid date-time.');
  return d;
}

function sendUpdates_(args) {
  var v = args.send_updates == null ? 'none' : args.send_updates;
  if (v !== 'all' && v !== 'none') throw bridgeError_('invalid_arguments', "send_updates must be 'all' or 'none'.");
  return v;
}

// ---------- Gmail helpers ----------

function splitAddresses_(value) {
  if (!value) return [];
  // Split on commas that are not inside quotes.
  var out = [], cur = '', quoted = false;
  for (var i = 0; i < value.length; i++) {
    var c = value.charAt(i);
    if (c === '"') quoted = !quoted;
    if (c === ',' && !quoted) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
    } else {
      cur += c;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function stripHtml_(html) {
  return String(html || '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function getMessage_(id) {
  var msg = GmailApp.getMessageById(id);
  if (!msg) throw notFound_('Email');
  return msg;
}

/** Labels shown for a message: conversation user labels + system states. */
function messageLabels_(msg) {
  var thread = msg.getThread();
  var labels = [];
  if (msg.isInTrash()) labels.push('TRASH');
  if (thread.isInInbox()) labels.push('INBOX');
  if (msg.isUnread()) labels.push('UNREAD');
  if (msg.isStarred()) labels.push('STARRED');
  if (thread.isImportant()) labels.push('IMPORTANT');
  thread.getLabels().forEach(function (l) { labels.push(l.getName()); });
  return labels;
}

function formatMessage_(msg) {
  var body = msg.getPlainBody() || stripHtml_(msg.getBody());
  var thread = msg.getThread();
  return {
    id: msg.getId(),
    thread_id: thread.getId(),
    thread_message_count: thread.getMessageCount(),
    subject: msg.getSubject(),
    from: msg.getFrom(),
    to: splitAddresses_(msg.getTo()),
    cc: splitAddresses_(msg.getCc()),
    reply_to: msg.getReplyTo() || '',
    date: msg.getDate().toISOString(),
    labels: messageLabels_(msg),
    unread: msg.isUnread(),
    snippet: body.slice(0, 160),
    body: body.length > MAX_BODY_CHARS ? body.slice(0, MAX_BODY_CHARS) : body,
    attachments: msg.getAttachments({ includeInlineImages: false }).map(function (a) {
      return { name: a.getName(), size: a.getSize(), mime_type: a.getContentType() };
    }),
    message_id_header: msg.getHeader('Message-ID') || '',
    references: msg.getHeader('References') || '',
    in_reply_to: msg.getHeader('In-Reply-To') || '',
  };
}

/** One entry per conversation: its latest message. */
function latestOfThreads_(threads) {
  return threads.map(function (t) {
    var msgs = t.getMessages();
    return formatMessage_(msgs[msgs.length - 1]);
  });
}

function getDraft_(id) {
  var draft = GmailApp.getDraft(id);
  if (!draft) throw notFound_('Draft');
  return draft;
}

function formatDraft_(draft) {
  var m = draft.getMessage();
  var body = m.getPlainBody() || stripHtml_(m.getBody());
  return {
    id: draft.getId(),
    message_id: m.getId(),
    thread_id: m.getThread().getId(),
    to: splitAddresses_(m.getTo()),
    cc: splitAddresses_(m.getCc()),
    bcc: splitAddresses_(m.getBcc()),
    subject: m.getSubject(),
    body: body.length > MAX_BODY_CHARS ? body.slice(0, MAX_BODY_CHARS) : body,
    in_reply_to: m.getHeader('In-Reply-To') || '',
    references: m.getHeader('References') || '',
  };
}

function composeArgs_(args) {
  return {
    to: emailList_(args, 'to'),
    cc: emailList_(args, 'cc'),
    bcc: emailList_(args, 'bcc'),
    subject: headerString_(args, 'subject', 250),
    body: optString_(args, 'body', 50000),
  };
}

function mailOptions_(m) {
  var options = {};
  if (m.cc.length) options.cc = m.cc.join(',');
  if (m.bcc.length) options.bcc = m.bcc.join(',');
  return options;
}

function findUserLabel_(name) {
  var lower = name.toLowerCase();
  var labels = GmailApp.getUserLabels();
  for (var i = 0; i < labels.length; i++) {
    if (labels[i].getName().toLowerCase() === lower) return labels[i]; // FR-17 case-insensitive
  }
  return null;
}

function applySystemLabel_(name, add, msg, thread) {
  switch (name) {
    case 'INBOX': return add ? thread.moveToInbox() : thread.moveToArchive();
    case 'UNREAD': return add ? msg.markUnread() : msg.markRead();
    case 'STARRED': return add ? msg.star() : msg.unstar();
    case 'IMPORTANT': return add ? thread.markImportant() : thread.markUnimportant();
  }
}

// ---------- Calendar helpers ----------

function calendar_() {
  return CalendarApp.getDefaultCalendar();
}

function timeZone_() {
  return calendar_().getTimeZone();
}

function isoInZone_(date, tz) {
  return Utilities.formatDate(date, tz, "yyyy-MM-dd'T'HH:mm:ssXXX");
}

function getEvent_(id) {
  var ev = calendar_().getEventById(id);
  if (!ev) throw notFound_('Event');
  return ev;
}

function formatEvent_(ev, tz) {
  var allDay = ev.isAllDayEvent();
  return {
    id: ev.getId(),
    title: ev.getTitle() || '',
    description: ev.getDescription() || '',
    location: ev.getLocation() || '',
    start: allDay ? Utilities.formatDate(ev.getAllDayStartDate(), tz, 'yyyy-MM-dd') : isoInZone_(ev.getStartTime(), tz),
    end: allDay ? Utilities.formatDate(ev.getAllDayEndDate(), tz, 'yyyy-MM-dd') : isoInZone_(ev.getEndTime(), tz),
    all_day: allDay,
    attendees: ev.getGuestList().map(function (g) {
      return { email: g.getEmail(), name: g.getName() || '', response_status: String(g.getGuestStatus()) };
    }),
    organizer: (ev.getCreators() || [])[0] || null,
    status: 'confirmed',
    transparency: 'opaque',
    html_link: '',
  };
}

function guestEmails_(ev) {
  return ev.getGuestList().map(function (g) { return g.getEmail(); });
}

/** CalendarApp can't send Google's update/cancel emails, so send our own. */
function emailAttendees_(recipients, subject, lines) {
  if (!recipients.length) return 0;
  GmailApp.sendEmail(recipients.join(','), subject, lines.join('\n'));
  return recipients.length;
}

function whenText_(start, end, tz) {
  return Utilities.formatDate(start, tz, 'EEE d MMM yyyy, HH:mm') + ' - ' +
    Utilities.formatDate(end, tz, 'HH:mm') + ' (' + tz + ')';
}

// ---------- Drive helpers ----------

function formatFile_(f) {
  return {
    id: f.getId(),
    name: f.getName(),
    mime_type: f.getMimeType(),
    size: f.getSize(),
    url: f.getUrl(),
    created: f.getDateCreated().toISOString(),
    updated: f.getLastUpdated().toISOString(),
    trashed: f.isTrashed(),
  };
}

function getOrCreateFolder_(name) {
  var it = DriveApp.getFoldersByName(name);
  while (it.hasNext()) {
    var folder = it.next();
    if (!folder.isTrashed()) return folder;
  }
  return DriveApp.createFolder(name);
}

// ---------- operations (allow-list) ----------

var OPERATIONS = {
  get_profile: function () {
    return { email: Session.getEffectiveUser().getEmail(), time_zone: timeZone_() };
  },

  // ----- Gmail -----
  get_latest_emails: function (args) {
    return latestOfThreads_(GmailApp.getInboxThreads(0, optInt_(args, 'max_results', 10, 1, 50)));
  },
  search_emails: function (args) {
    var threads = GmailApp.search(reqString_(args, 'query', 1000), 0, optInt_(args, 'max_results', 10, 1, 50));
    return latestOfThreads_(threads);
  },
  count_emails: function (args) {
    // GmailApp.search returns at most 500 threads per call, so page through.
    var query = reqString_(args, 'query', 1000);
    var total = 0;
    var page;
    do {
      page = GmailApp.search(query, total, 500).length;
      total += page;
    } while (page === 500 && total < MAX_COUNT);
    return { conversations: total, capped: page === 500 && total >= MAX_COUNT };
  },
  read_email: function (args) {
    return formatMessage_(getMessage_(reqString_(args, 'message_id', 200)));
  },
  read_thread: function (args) {
    var thread = GmailApp.getThreadById(reqString_(args, 'thread_id', 200));
    if (!thread) throw notFound_('Conversation');
    var messages = thread.getMessages().map(formatMessage_);
    return { thread_id: thread.getId(), subject: thread.getFirstMessageSubject(), messages: messages };
  },
  list_drafts: function (args) {
    return GmailApp.getDrafts().slice(0, optInt_(args, 'max_results', 10, 1, 50)).map(formatDraft_);
  },
  get_draft: function (args) {
    return formatDraft_(getDraft_(reqString_(args, 'draft_id', 200)));
  },
  list_gmail_labels: function () {
    var system = SYSTEM_LABELS.map(function (n) { return { id: n, name: n, type: 'system' }; });
    return system.concat(GmailApp.getUserLabels().map(function (l) {
      return { id: l.getName(), name: l.getName(), type: 'user' };
    }));
  },
  create_draft: function (args) {
    var m = composeArgs_(args);
    var draft;
    if (args.reply_to_message_id) {
      // Reply in the original conversation; Gmail sets In-Reply-To/References.
      var original = getMessage_(reqString_(args, 'reply_to_message_id', 200));
      var options = mailOptions_(m);
      if (m.subject) options.subject = m.subject;
      draft = original.createDraftReply(m.body, options);
    } else {
      draft = GmailApp.createDraft(m.to.join(','), m.subject, m.body, mailOptions_(m));
    }
    return { draft_id: draft.getId(), message_id: draft.getMessageId() };
  },
  update_draft: function (args) {
    var draft = getDraft_(reqString_(args, 'draft_id', 200));
    var m = composeArgs_(args);
    draft.update(m.to.join(','), m.subject, m.body, mailOptions_(m));
    return { draft_id: draft.getId() };
  },
  delete_draft: function (args) {
    getDraft_(reqString_(args, 'draft_id', 200)).deleteDraft();
    return { deleted: true };
  },
  send_draft: function (args) {
    var sent = getDraft_(reqString_(args, 'draft_id', 200)).send();
    return { message_id: sent.getId(), thread_id: sent.getThread().getId() };
  },
  send_email: function (args) {
    var m = composeArgs_(args);
    if (!m.to.length) throw bridgeError_('invalid_arguments', 'to needs at least one recipient.');
    GmailApp.sendEmail(m.to.join(','), m.subject, m.body, mailOptions_(m));
    return { sent: true };
  },
  update_email_labels: function (args) {
    var msg = getMessage_(reqString_(args, 'message_id', 200));
    var thread = msg.getThread();
    var add = stringList_(args, 'add_labels'), remove = stringList_(args, 'remove_labels');
    if (!add.length && !remove.length) throw bridgeError_('invalid_arguments', 'Give labels to add or remove.');
    // Resolve everything first so a bad name changes nothing.
    var removeUser = remove.map(function (name) {
      if (SYSTEM_LABELS.indexOf(name.toUpperCase()) !== -1) return null;
      var label = findUserLabel_(name);
      if (!label) throw bridgeError_('not_found', 'Label not found: ' + name);
      return label;
    });
    add.forEach(function (name) {
      var sys = name.toUpperCase();
      if (SYSTEM_LABELS.indexOf(sys) !== -1) return applySystemLabel_(sys, true, msg, thread);
      (findUserLabel_(name) || GmailApp.createLabel(name)).addToThread(thread);
    });
    remove.forEach(function (name, i) {
      if (removeUser[i]) removeUser[i].removeFromThread(thread);
      else applySystemLabel_(name.toUpperCase(), false, msg, thread);
    });
    return { labels: messageLabels_(getMessage_(msg.getId())) };
  },
  trash_email: function (args) {
    getMessage_(reqString_(args, 'message_id', 200)).moveToTrash();
    return { trashed: true };
  },
  restore_email: function (args) {
    var msg = getMessage_(reqString_(args, 'message_id', 200));
    if (!msg.isInTrash()) throw bridgeError_('invalid_arguments', 'That email is not in Trash.');
    var thread = msg.getThread();
    thread.moveToInbox(); // restores the conversation from Trash
    return { restored: true, thread_message_count: thread.getMessageCount() };
  },

  // ----- Calendar -----
  list_calendar_events: function (args) {
    var tz = timeZone_();
    var events = calendar_().getEvents(dateArg_(args, 'start', true), dateArg_(args, 'end', true),
      args.query ? { search: reqString_(args, 'query', 200) } : undefined);
    var max = optInt_(args, 'max_results', 25, 1, 100);
    return { time_zone: tz, events: events.slice(0, max).map(function (ev) { return formatEvent_(ev, tz); }) };
  },
  get_calendar_event: function (args) {
    return formatEvent_(getEvent_(reqString_(args, 'event_id', 300)), timeZone_());
  },
  check_availability: function (args) {
    var tz = timeZone_();
    var busy = calendar_().getEvents(dateArg_(args, 'start', true), dateArg_(args, 'end', true))
      .filter(function (ev) { return !ev.isAllDayEvent(); })
      .map(function (ev) { return { start: isoInZone_(ev.getStartTime(), tz), end: isoInZone_(ev.getEndTime(), tz) }; });
    return { time_zone: tz, busy: busy };
  },
  create_calendar_event: function (args) {
    var title = reqString_(args, 'title', 300);
    var start = dateArg_(args, 'start', true), end = dateArg_(args, 'end', true);
    if (end <= start) throw bridgeError_('invalid_arguments', 'end must be after start.');
    var attendees = emailList_(args, 'attendees');
    var ev = calendar_().createEvent(title, start, end, {
      description: optString_(args, 'description', 8000),
      location: optString_(args, 'location', 500),
      guests: attendees.join(','),
      sendInvites: sendUpdates_(args) === 'all' && attendees.length > 0,
    });
    return { id: ev.getId(), html_link: '' };
  },
  update_calendar_event: function (args) {
    // Validate everything before touching the calendar (NFR-07).
    var id = reqString_(args, 'event_id', 300);
    var notify = sendUpdates_(args) === 'all';
    var start = dateArg_(args, 'start', false), end = dateArg_(args, 'end', false);
    var fields = ['title', 'description', 'location', 'start', 'end', 'attendees'];
    if (!fields.some(function (f) { return args[f] != null; })) throw bridgeError_('invalid_arguments', 'Nothing to update.');
    var ev = getEvent_(id);
    var changed = [];
    if (args.title != null) { ev.setTitle(reqString_(args, 'title', 300)); changed.push('title'); }
    if (args.description != null) { ev.setDescription(optString_(args, 'description', 8000)); changed.push('description'); }
    if (args.location != null) { ev.setLocation(optString_(args, 'location', 500)); changed.push('location'); }
    if (start || end) {
      var newStart = start || ev.getStartTime(), newEnd = end || ev.getEndTime();
      if (newEnd <= newStart) throw bridgeError_('invalid_arguments', 'end must be after start.');
      ev.setTime(newStart, newEnd);
      changed.push('time');
    }
    if (args.attendees != null) {
      var wanted = emailList_(args, 'attendees');
      var lowerWanted = wanted.map(function (e) { return e.toLowerCase(); });
      var current = guestEmails_(ev);
      var lowerCurrent = current.map(function (e) { return e.toLowerCase(); });
      current.forEach(function (e) { if (lowerWanted.indexOf(e.toLowerCase()) === -1) ev.removeGuest(e); });
      wanted.forEach(function (e) { if (lowerCurrent.indexOf(e.toLowerCase()) === -1) ev.addGuest(e); });
      changed.push('attendees');
    }
    if (!changed.length) throw bridgeError_('invalid_arguments', 'Nothing to update.');
    var notified = 0;
    if (notify) {
      var tz = timeZone_();
      notified = emailAttendees_(guestEmails_(ev), 'Updated: ' + ev.getTitle(), [
        'The meeting "' + ev.getTitle() + '" has been updated (' + changed.join(', ') + ').',
        '',
        'When: ' + whenText_(ev.getStartTime(), ev.getEndTime(), tz),
        ev.getLocation() ? 'Where: ' + ev.getLocation() : '',
      ]);
    }
    return { id: ev.getId(), html_link: '', notified: notified };
  },
  delete_calendar_event: function (args) {
    var id = reqString_(args, 'event_id', 300);
    var notify = sendUpdates_(args) === 'all';
    var ev = getEvent_(id);
    var tz = timeZone_();
    var guests = guestEmails_(ev), title = ev.getTitle();
    var when = ev.isAllDayEvent() ? 'All day' : whenText_(ev.getStartTime(), ev.getEndTime(), tz);
    ev.deleteEvent();
    var notified = notify
      ? emailAttendees_(guests, 'Cancelled: ' + title, ['The meeting "' + title + '" (' + when + ') has been cancelled.'])
      : 0;
    return { deleted: true, notified: notified };
  },

  // ----- Drive -----
  list_drive_files: function (args) {
    var max = optInt_(args, 'max_results', 20, 1, 100);
    var q = 'trashed = false';
    if (args.name_contains) q += " and title contains '" + reqString_(args, 'name_contains', 200).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
    var it = DriveApp.searchFiles(q), out = [];
    while (it.hasNext() && out.length < max) out.push(formatFile_(it.next()));
    out.sort(function (a, b) { return a.updated < b.updated ? 1 : -1; });
    return out;
  },
  get_drive_file: function (args) {
    return formatFile_(DriveApp.getFileById(reqString_(args, 'file_id', 200)));
  },
  save_attachments_to_drive: function (args) {
    var msg = getMessage_(reqString_(args, 'message_id', 200));
    var wanted = stringList_(args, 'attachment_names').map(function (n) { return n.toLowerCase(); });
    var folderName = args.folder_name == null ? DEFAULT_FOLDER : reqString_(args, 'folder_name', 100);
    var folder = getOrCreateFolder_(folderName);
    var saved = [], skipped = [];
    msg.getAttachments({ includeInlineImages: false }).forEach(function (att) {
      var name = att.getName(), size = att.getSize();
      if (wanted.length && wanted.indexOf(name.toLowerCase()) === -1) return;
      if (size > MAX_ATTACHMENT_BYTES) {
        skipped.push({ name: name, size: size, reason: 'larger than 25 MB' });
        return;
      }
      var file = folder.createFile(att.copyBlob());
      saved.push({ name: name, size: size, file_id: file.getId(), url: file.getUrl() });
    });
    return { folder: { id: folder.getId(), name: folder.getName(), url: folder.getUrl() }, saved: saved, skipped: skipped };
  },
  trash_drive_file: function (args) {
    DriveApp.getFileById(reqString_(args, 'file_id', 200)).setTrashed(true);
    return { trashed: true };
  },
};

// Exposed for tests / allow-list parity with the backend.
var ALLOWED_OPERATIONS = Object.keys(OPERATIONS);
