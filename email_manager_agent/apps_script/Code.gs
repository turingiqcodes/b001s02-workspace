/**
 * Email Manager Agent — Google Apps Script bridge.
 *
 * The local FastAPI backend POSTs {secret, action, params} to this Web App.
 * Every response is {ok: true, data} or {ok: false, error: {code, message}}.
 *
 * Setup (see docs/APPS_SCRIPT_SETUP.md):
 *   1. Project Settings > Script Properties > BRIDGE_SHARED_SECRET = a long random string
 *      (the same value as APPS_SCRIPT_SHARED_SECRET in the backend's .env).
 *   2. Run checkSetup() once from the editor and authorize.
 *   3. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone.
 *
 * Uses the Gmail v1, Calendar v3 and Drive v3 advanced services.
 * There are deliberately no delete or trash operations for email or files.
 * The backend validates every request, and this script validates them again.
 */

const SECRET_PROPERTY = 'BRIDGE_SHARED_SECRET';
const MIN_SECRET_LENGTH = 16;
const MAX_RESULTS = 25;
const MAX_EVENT_RESULTS = 250;
const MAX_RECIPIENTS = 20;
const MAX_MESSAGE_IDS = 100;
const MAX_BODY_CHARS = 100000;
const MAX_READ_BODY_CHARS = 50000;
const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
const MAX_EVENT_SPAN_DAYS = 62;
const DEFAULT_FOLDER = 'Email Attachments';
const FOLDER_MIME = 'application/vnd.google-apps.folder';
const META_HEADERS = ['From', 'To', 'Cc', 'Subject', 'Date'];
const CHANGEABLE_SYSTEM_LABELS = ['INBOX', 'UNREAD', 'STARRED', 'IMPORTANT'];
const FILE_FIELDS = 'id,name,mimeType,modifiedTime,size,webViewLink,parents';
const DRIVE_MIME_TYPES = {
  pdf: "mimeType = 'application/pdf'",
  document: "mimeType = 'application/vnd.google-apps.document'",
  spreadsheet: "mimeType = 'application/vnd.google-apps.spreadsheet'",
  presentation: "mimeType = 'application/vnd.google-apps.presentation'",
  image: "mimeType contains 'image/'",
  folder: "mimeType = '" + FOLDER_MIME + "'",
};

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

function doGet() {
  return ContentService.createTextOutput('Email Manager bridge is running. Use POST.');
}

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e && e.postData ? e.postData.contents : '');
  } catch (err) {
    return json_({ ok: false, error: { code: 'invalid_params', message: 'Request body must be JSON.' } });
  }
  if (!req || typeof req !== 'object' || Array.isArray(req)) {
    return json_({ ok: false, error: { code: 'invalid_params', message: 'Request body must be a JSON object.' } });
  }

  const expected = PropertiesService.getScriptProperties().getProperty(SECRET_PROPERTY);
  if (!expected) {
    return json_({ ok: false, error: { code: 'not_configured',
      message: 'The ' + SECRET_PROPERTY + ' script property is not set. Add it in Project Settings and run checkSetup.' } });
  }
  if (typeof req.secret !== 'string' || !safeEqual_(req.secret, expected)) {
    return json_({ ok: false, error: { code: 'unauthorized', message: 'Invalid shared secret.' } });
  }

  const action = typeof req.action === 'string' && Object.prototype.hasOwnProperty.call(ACTIONS, req.action)
    ? ACTIONS[req.action] : null;
  if (!action) {
    return json_({ ok: false, error: { code: 'unknown_action', message: 'Unknown action: ' + String(req.action) } });
  }

  try {
    const params = validate_(action.spec, req.params === undefined || req.params === null ? {} : req.params);
    return json_({ ok: true, data: action.run(params) });
  } catch (err) {
    const out = toError_(err);
    if (out.code === 'internal') console.error(req.action, err && err.stack ? err.stack : err);
    return json_({ ok: false, error: out });
  }
}

/**
 * Run this once from the Apps Script editor (select checkSetup, then Run).
 * It triggers Google's authorization prompt and logs what is and isn't set up.
 * The secret's value is never logged.
 */
function checkSetup() {
  const report = { secret: 'missing', gmail: 'not checked', calendar: 'not checked', drive: 'not checked' };
  const secret = PropertiesService.getScriptProperties().getProperty(SECRET_PROPERTY);
  if (!secret) report.secret = 'missing: add the ' + SECRET_PROPERTY + ' script property';
  else if (secret.length < MIN_SECRET_LENGTH) report.secret = 'too short: use at least ' + MIN_SECRET_LENGTH + ' characters';
  else if (secret !== secret.trim()) report.secret = 'has leading or trailing spaces: remove them';
  else report.secret = 'set (' + secret.length + ' characters)';

  try { report.gmail = 'ok (' + Gmail.Users.getProfile('me').emailAddress + ')'; } catch (err) { report.gmail = 'error: ' + errMessage_(err); }
  try { Calendar.Events.list('primary', { maxResults: 1 }); report.calendar = 'ok'; } catch (err) { report.calendar = 'error: ' + errMessage_(err); }
  try { Drive.Files.list({ pageSize: 1, fields: 'files(id)' }); report.drive = 'ok'; } catch (err) { report.drive = 'error: ' + errMessage_(err); }

  Object.keys(report).forEach(function (k) { console.log(k + ': ' + report[k]); });
  report.ready = report.secret.indexOf('set (') === 0 &&
    report.gmail.indexOf('ok') === 0 && report.calendar === 'ok' && report.drive === 'ok';
  console.log(report.ready ? 'Setup looks good. Deploy as a Web App next.' : 'Setup is not complete yet. Fix the items above.');
  return report;
}

// ---------------------------------------------------------------------------
// Action table: parameter spec + handler
// ---------------------------------------------------------------------------

const S = {
  str: function (opts) { return Object.assign({ t: 'string', max: 1000 }, opts || {}); },
  int: function (min, max, def) { return { t: 'int', min: min, max: max, def: def }; },
  bool: function (def) { return { t: 'bool', def: def }; },
  emails: function (opts) { return Object.assign({ t: 'emails', max: MAX_RECIPIENTS }, opts || {}); },
  strings: function (opts) { return Object.assign({ t: 'strings', max: 100 }, opts || {}); },
  iso: function (opts) { return Object.assign({ t: 'iso' }, opts || {}); },
  date: function (opts) { return Object.assign({ t: 'date' }, opts || {}); },
  oneOf: function (values, def) { return { t: 'enum', values: values, def: def }; },
};

const ID = { max: 200, pattern: /^[A-Za-z0-9_\-.@]+$/ };

const ACTIONS = {
  ping: { spec: {}, run: ping },
  search_emails: { spec: { query: S.str({ max: 500 }), max_results: S.int(1, MAX_RESULTS, 10) }, run: searchEmails },
  read_email: { spec: { message_id: S.str(Object.assign({ req: true }, ID)) }, run: readEmail },
  list_labels: { spec: {}, run: listLabels },
  list_drafts: { spec: { max_results: S.int(1, MAX_RESULTS, 10) }, run: listDrafts },
  get_draft: { spec: { draft_id: S.str(Object.assign({ req: true }, ID)) }, run: getDraft },
  send_email: {
    spec: { to: S.emails({ req: true }), subject: S.str({ req: true, max: 998, allowEmpty: true }),
      body: S.str({ req: true, max: MAX_BODY_CHARS, allowEmpty: true }), cc: S.emails(), bcc: S.emails() },
    run: sendEmail,
  },
  reply_to_email: {
    spec: { message_id: S.str(Object.assign({ req: true }, ID)), body: S.str({ req: true, max: MAX_BODY_CHARS }),
      reply_all: S.bool(false) },
    run: replyToEmail,
  },
  forward_email: {
    spec: { message_id: S.str(Object.assign({ req: true }, ID)), to: S.emails({ req: true }),
      note: S.str({ max: MAX_BODY_CHARS, allowEmpty: true }) },
    run: forwardEmail,
  },
  create_draft: {
    spec: { to: S.emails({ req: true }), subject: S.str({ req: true, max: 998, allowEmpty: true }),
      body: S.str({ req: true, max: MAX_BODY_CHARS, allowEmpty: true }), cc: S.emails(), bcc: S.emails(),
      reply_to_message_id: S.str(ID) },
    run: createDraft,
  },
  update_draft: {
    spec: { draft_id: S.str(Object.assign({ req: true }, ID)), to: S.emails(), subject: S.str({ max: 998, allowEmpty: true }),
      body: S.str({ max: MAX_BODY_CHARS, allowEmpty: true }), cc: S.emails(), bcc: S.emails() },
    run: updateDraft,
  },
  send_draft: { spec: { draft_id: S.str(Object.assign({ req: true }, ID)) }, run: sendDraft },
  create_label: { spec: { name: S.str({ req: true, max: 225 }) }, run: createLabel },
  modify_labels: {
    spec: { message_ids: S.strings({ req: true, min: 1, max: MAX_MESSAGE_IDS, item: ID }),
      add_labels: S.strings({ max: 20, item: { max: 225 } }), remove_labels: S.strings({ max: 20, item: { max: 225 } }) },
    run: modifyLabels,
  },
  list_events: {
    spec: { start: S.iso({ req: true }), end: S.iso({ req: true }), query: S.str({ max: 200 }),
      max_results: S.int(1, MAX_EVENT_RESULTS, 25) },
    run: listEvents,
  },
  get_event: { spec: { event_id: S.str(Object.assign({ req: true }, ID)) }, run: getEvent },
  create_event: {
    spec: { title: S.str({ req: true, max: 500 }), start: S.iso({ req: true }), end: S.iso({ req: true }),
      attendees: S.emails({ max: 50 }), description: S.str({ max: 8000, allowEmpty: true }), location: S.str({ max: 500, allowEmpty: true }) },
    run: createEvent,
  },
  move_event: {
    spec: { event_id: S.str(Object.assign({ req: true }, ID)), new_start: S.iso({ req: true }), new_end: S.iso({ req: true }) },
    run: moveEvent,
  },
  cancel_event: {
    spec: { event_id: S.str(Object.assign({ req: true }, ID)), notify_attendees: S.bool(true) },
    run: cancelEvent,
  },
  search_drive_files: {
    spec: { name_contains: S.str({ max: 200 }), mime_type: S.oneOf(['any'].concat(Object.keys(DRIVE_MIME_TYPES)), 'any'),
      modified_after: S.date(), folder_id: S.str(ID), max_results: S.int(1, MAX_RESULTS, 10) },
    run: searchDriveFiles,
  },
  get_file: { spec: { file_id: S.str(Object.assign({ req: true }, ID)) }, run: getFile },
  create_drive_folder: { spec: { name: S.str({ req: true, max: 255 }), parent_folder_id: S.str(ID) }, run: createDriveFolder },
  move_drive_file: {
    spec: { file_id: S.str(Object.assign({ req: true }, ID)), folder_id: S.str(Object.assign({ req: true }, ID)) },
    run: moveDriveFile,
  },
  save_attachments_to_drive: {
    spec: { message_id: S.str(Object.assign({ req: true }, ID)), attachment_ids: S.strings({ max: 50, item: { max: 100 } }),
      folder_id: S.str(ID), folder_name: S.str({ max: 255 }) },
    run: saveAttachmentsToDrive,
  },
};

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

function ping() {
  const profile = Gmail.Users.getProfile('me');
  let tz = '';
  // Events.list reports the calendar's time zone and only needs the calendar.events scope.
  try { tz = Calendar.Events.list('primary', { maxResults: 1 }).timeZone || ''; } catch (err) { tz = ''; }
  return { ok: true, email: profile.emailAddress, timezone: tz || Session.getScriptTimeZone(), time: new Date().toISOString() };
}

function myAddress_() {
  return String(Gmail.Users.getProfile('me').emailAddress || '').toLowerCase();
}

// ---------------------------------------------------------------------------
// Gmail — read
// ---------------------------------------------------------------------------

function labelMap_() {
  const map = {};
  (Gmail.Users.Labels.list('me').labels || []).forEach(function (l) { map[l.id] = l; });
  return map;
}

function header_(payload, name) {
  const h = ((payload && payload.headers) || []).find(function (x) { return x.name.toLowerCase() === name.toLowerCase(); });
  return h ? h.value : '';
}

function msgDate_(msg) {
  if (msg.internalDate) return new Date(Number(msg.internalDate)).toISOString();
  const d = new Date(header_(msg.payload, 'Date'));
  return isNaN(d.getTime()) ? null : d.toISOString();
}

function summary_(msg, labels) {
  const p = msg.payload || {};
  const ids = msg.labelIds || [];
  return {
    id: msg.id,
    thread_id: msg.threadId,
    from: header_(p, 'From'),
    to: header_(p, 'To'),
    subject: header_(p, 'Subject'),
    date: msgDate_(msg),
    snippet: msg.snippet || '',
    labels: ids.map(function (id) { return labels[id] ? labels[id].name : id; }),
    unread: ids.indexOf('UNREAD') !== -1,
    has_attachments: /^multipart\/mixed/i.test(p.mimeType || ''),
  };
}

function searchEmails(params) {
  const list = Gmail.Users.Messages.list('me', { q: params.query || 'in:inbox', maxResults: params.max_results });
  const labels = labelMap_();
  const messages = (list.messages || []).map(function (m) {
    const msg = Gmail.Users.Messages.get('me', m.id, { format: 'metadata', metadataHeaders: META_HEADERS });
    return summary_(msg, labels);
  });
  return { messages: messages, result_size_estimate: list.resultSizeEstimate || messages.length };
}

function decode_(data) {
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(data)).getDataAsString('UTF-8');
}

function walkParts_(part, out) {
  if (!part) return;
  const mime = part.mimeType || '';
  if (part.filename && part.body && (part.body.attachmentId || part.body.data)) {
    // partId is stable across fetches; Gmail's attachmentId is not.
    out.attachments.push({ attachment_id: part.partId, filename: part.filename, mime_type: mime, size: part.body.size || 0, part: part });
  } else if (mime === 'text/plain' && part.body && part.body.data && !out.plain) {
    out.plain = decode_(part.body.data);
  } else if (mime === 'text/html' && part.body && part.body.data && !out.html) {
    out.html = decode_(part.body.data);
  }
  (part.parts || []).forEach(function (p) { walkParts_(p, out); });
}

function stripHtml_(html) {
  return html
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|tr|li|h\d)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function parsed_(msg) {
  const out = { plain: '', html: '', attachments: [] };
  walkParts_(msg.payload, out);
  out.body = out.plain || stripHtml_(out.html || '');
  return out;
}

function publicAttachment_(a) {
  return { attachment_id: a.attachment_id, filename: a.filename, mime_type: a.mime_type, size: a.size };
}

function readEmail(params) {
  const msg = Gmail.Users.Messages.get('me', params.message_id, { format: 'full' });
  const parts = parsed_(msg);
  let body = parts.body;
  if (body.length > MAX_READ_BODY_CHARS) {
    body = body.slice(0, MAX_READ_BODY_CHARS) + '\n\n[Body truncated: ' + (body.length - MAX_READ_BODY_CHARS) + ' more characters]';
  }
  const s = summary_(msg, labelMap_());
  return {
    id: s.id, thread_id: s.thread_id, from: s.from, to: s.to, cc: header_(msg.payload, 'Cc'),
    subject: s.subject, date: s.date, labels: s.labels, body: body,
    attachments: parts.attachments.map(publicAttachment_),
  };
}

function listLabels() {
  return {
    labels: (Gmail.Users.Labels.list('me').labels || []).map(function (l) {
      return { id: l.id, name: l.name, type: l.type === 'system' ? 'system' : 'user' };
    }),
  };
}

function draftOut_(draft) {
  const msg = draft.message || {};
  const p = msg.payload || {};
  return {
    id: draft.id,
    message_id: msg.id || null,
    thread_id: msg.threadId || null,
    to: extractEmails_(header_(p, 'To')),
    cc: extractEmails_(header_(p, 'Cc')),
    bcc: extractEmails_(header_(p, 'Bcc')),
    subject: header_(p, 'Subject'),
    body: parsed_(msg).body,
    snippet: msg.snippet || '',
    in_reply_to: header_(p, 'In-Reply-To') || null,
    references: header_(p, 'References') || null,
  };
}

function listDrafts(params) {
  const list = Gmail.Users.Drafts.list('me', { maxResults: params.max_results });
  return {
    drafts: (list.drafts || []).map(function (d) {
      const full = draftOut_(Gmail.Users.Drafts.get('me', d.id, { format: 'full' }));
      return { id: full.id, message_id: full.message_id, to: full.to, subject: full.subject, snippet: full.snippet };
    }),
  };
}

function getDraft(params) {
  const d = draftOut_(Gmail.Users.Drafts.get('me', params.draft_id, { format: 'full' }));
  return { id: d.id, message_id: d.message_id, to: d.to, cc: d.cc, bcc: d.bcc, subject: d.subject, body: d.body };
}

// ---------------------------------------------------------------------------
// Gmail — write
// ---------------------------------------------------------------------------

function encodeHeader_(value) {
  value = String(value || '').replace(/[\r\n]+/g, ' ');
  return /^[\x20-\x7e]*$/.test(value)
    ? value
    : '=?UTF-8?B?' + Utilities.base64Encode(value, Utilities.Charset.UTF_8) + '?=';
}

function wrap76_(s) {
  const out = [];
  for (let i = 0; i < s.length; i += 76) out.push(s.slice(i, i + 76));
  return out.join('\r\n');
}

function webSafeToStd_(data) {
  let s = String(data).replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, '');
  while (s.length % 4) s += '=';
  return s;
}

/**
 * Build an RFC 2822 message and return it base64url-encoded for the Gmail API.
 * m: {to[], cc[], bcc[], subject, body, inReplyTo, references, attachments[{filename, mimeType, data (base64url)}]}
 */
function buildRaw_(m) {
  const headers = [];
  headers.push('To: ' + (m.to || []).join(', '));
  if (m.cc && m.cc.length) headers.push('Cc: ' + m.cc.join(', '));
  if (m.bcc && m.bcc.length) headers.push('Bcc: ' + m.bcc.join(', '));
  headers.push('Subject: ' + encodeHeader_(m.subject || ''));
  if (m.inReplyTo) headers.push('In-Reply-To: ' + m.inReplyTo);
  if (m.references) headers.push('References: ' + m.references);
  headers.push('MIME-Version: 1.0');

  const textPart = [
    'Content-Type: text/plain; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
    '',
    wrap76_(Utilities.base64Encode(m.body || '', Utilities.Charset.UTF_8)),
  ];

  let lines;
  if (m.attachments && m.attachments.length) {
    const boundary = 'emagent_' + Date.now().toString(36) + '_' + Math.floor(Math.random() * 1e9).toString(36);
    lines = headers.concat(['Content-Type: multipart/mixed; boundary="' + boundary + '"', '', '--' + boundary]).concat(textPart);
    m.attachments.forEach(function (a) {
      const name = encodeHeader_(a.filename).replace(/"/g, '');
      lines.push('--' + boundary);
      lines.push('Content-Type: ' + (a.mimeType || 'application/octet-stream') + '; name="' + name + '"');
      lines.push('Content-Disposition: attachment; filename="' + name + '"');
      lines.push('Content-Transfer-Encoding: base64');
      lines.push('');
      lines.push(wrap76_(webSafeToStd_(a.data)));
    });
    lines.push('--' + boundary + '--');
  } else {
    lines = headers.concat(textPart);
  }
  return Utilities.base64EncodeWebSafe(lines.join('\r\n'), Utilities.Charset.UTF_8);
}

function checkRecipientCount_(params) {
  const n = (params.to || []).length + (params.cc || []).length + (params.bcc || []).length;
  if (n > MAX_RECIPIENTS) fail_('invalid_params', 'At most ' + MAX_RECIPIENTS + ' recipients are allowed in total.');
}

function replyInfo_(messageId) {
  if (!messageId) return {};
  const msg = Gmail.Users.Messages.get('me', messageId, {
    format: 'metadata', metadataHeaders: ['Message-ID', 'References', 'Subject', 'From', 'To', 'Cc', 'Reply-To'],
  });
  const p = msg.payload || {};
  const msgId = header_(p, 'Message-ID');
  const refs = header_(p, 'References');
  return {
    threadId: msg.threadId,
    inReplyTo: msgId || null,
    references: [refs, msgId].filter(String).join(' ') || null,
    subject: header_(p, 'Subject'),
    from: header_(p, 'From'),
    to: header_(p, 'To'),
    cc: header_(p, 'Cc'),
    replyTo: header_(p, 'Reply-To'),
  };
}

function prefixSubject_(prefix, subject) {
  subject = subject || '';
  return new RegExp('^' + prefix + ':', 'i').test(subject.trim()) ? subject : prefix + ': ' + subject;
}

function sendEmail(params) {
  checkRecipientCount_(params);
  const res = Gmail.Users.Messages.send({
    raw: buildRaw_({ to: params.to, cc: params.cc, bcc: params.bcc, subject: params.subject, body: params.body }),
  }, 'me');
  return { message_id: res.id, thread_id: res.threadId };
}

function replyToEmail(params) {
  const info = replyInfo_(params.message_id);
  const me = myAddress_();
  const to = extractEmails_(info.replyTo || info.from).filter(function (a) { return a !== me; });
  let cc = [];
  if (params.reply_all) {
    cc = extractEmails_(info.to + ',' + info.cc).filter(function (a) { return a !== me && to.indexOf(a) === -1; });
  }
  if (!to.length) {
    // Replying to your own message: send to its original recipients instead.
    extractEmails_(info.to).forEach(function (a) { if (a !== me && to.indexOf(a) === -1) to.push(a); });
    cc = cc.filter(function (a) { return to.indexOf(a) === -1; });
  }
  if (!to.length) fail_('invalid_params', 'Could not work out who to reply to.');
  checkRecipientCount_({ to: to, cc: cc });
  const res = Gmail.Users.Messages.send({
    raw: buildRaw_({ to: to, cc: cc, subject: prefixSubject_('Re', info.subject), body: params.body,
      inReplyTo: info.inReplyTo, references: info.references }),
    threadId: info.threadId,
  }, 'me');
  return { message_id: res.id, thread_id: res.threadId, to: to, cc: cc };
}

function forwardEmail(params) {
  const msg = Gmail.Users.Messages.get('me', params.message_id, { format: 'full' });
  const p = msg.payload || {};
  const parts = parsed_(msg);
  const attachments = [];
  const skipped = [];
  let total = 0;
  parts.attachments.forEach(function (a) {
    if (a.size > MAX_ATTACHMENT_BYTES || total + a.size > MAX_ATTACHMENT_BYTES) {
      skipped.push({ filename: a.filename, reason: 'larger than the ' + (MAX_ATTACHMENT_BYTES / 1048576) + ' MB limit' });
      return;
    }
    total += a.size;
    attachments.push({ filename: a.filename, mimeType: a.mime_type, data: attachmentData_(params.message_id, a.part) });
  });
  const intro = [
    params.note || '',
    '',
    '---------- Forwarded message ---------',
    'From: ' + header_(p, 'From'),
    'Date: ' + header_(p, 'Date'),
    'Subject: ' + header_(p, 'Subject'),
    'To: ' + header_(p, 'To'),
    '',
  ].join('\n');
  const res = Gmail.Users.Messages.send({
    raw: buildRaw_({ to: params.to, subject: prefixSubject_('Fwd', header_(p, 'Subject')), body: intro + '\n' + parts.body,
      attachments: attachments }),
  }, 'me');
  return { message_id: res.id, thread_id: res.threadId, skipped_attachments: skipped };
}

function createDraft(params) {
  checkRecipientCount_(params);
  const reply = replyInfo_(params.reply_to_message_id);
  const message = {
    raw: buildRaw_({ to: params.to, cc: params.cc, bcc: params.bcc, subject: params.subject, body: params.body,
      inReplyTo: reply.inReplyTo, references: reply.references }),
  };
  if (reply.threadId) message.threadId = reply.threadId;
  const draft = Gmail.Users.Drafts.create({ message: message }, 'me');
  return { draft_id: draft.id, message_id: draft.message ? draft.message.id : null };
}

function updateDraft(params) {
  const changes = ['to', 'subject', 'body', 'cc', 'bcc'].filter(function (k) { return params[k] !== undefined; });
  if (!changes.length) fail_('invalid_params', 'Nothing to change in the draft.');
  const cur = draftOut_(Gmail.Users.Drafts.get('me', params.draft_id, { format: 'full' }));
  const merged = {};
  ['to', 'cc', 'bcc', 'subject', 'body'].forEach(function (k) { merged[k] = params[k] !== undefined ? params[k] : cur[k]; });
  checkRecipientCount_(merged);
  merged.inReplyTo = cur.in_reply_to;
  merged.references = cur.references;
  const message = { raw: buildRaw_(merged) };
  if (cur.thread_id) message.threadId = cur.thread_id;
  const draft = Gmail.Users.Drafts.update({ id: params.draft_id, message: message }, 'me', params.draft_id);
  return { draft_id: draft.id || params.draft_id, message_id: draft.message ? draft.message.id : null };
}

function sendDraft(params) {
  const res = Gmail.Users.Drafts.send({ id: params.draft_id }, 'me');
  return { message_id: res.id, thread_id: res.threadId };
}

function createLabel(params) {
  const name = params.name.trim();
  const all = Gmail.Users.Labels.list('me').labels || [];
  const clash = all.find(function (l) { return l.name.toLowerCase() === name.toLowerCase() || l.id.toLowerCase() === name.toLowerCase(); });
  if (clash) fail_('invalid_params', "A label named '" + clash.name + "' already exists.");
  const l = Gmail.Users.Labels.create({ name: name, labelListVisibility: 'labelShow', messageListVisibility: 'show' }, 'me');
  return { id: l.id, name: l.name };
}

function modifyLabels(params) {
  const add = params.add_labels || [];
  const remove = params.remove_labels || [];
  if (!add.length && !remove.length) fail_('invalid_params', 'No labels to add or remove.');
  const all = Gmail.Users.Labels.list('me').labels || [];
  function resolve(name) {
    const key = String(name).trim().toLowerCase();
    const l = all.find(function (x) { return x.name.toLowerCase() === key || x.id.toLowerCase() === key; });
    if (!l) fail_('not_found', "Label '" + name + "' does not exist.");
    if (l.type === 'system' && CHANGEABLE_SYSTEM_LABELS.indexOf(l.id) === -1) {
      fail_('invalid_params', "'" + l.name + "' is a system label and can't be changed. Allowed system labels: " +
        CHANGEABLE_SYSTEM_LABELS.join(', ') + '.');
    }
    return l.id;
  }
  const addIds = add.map(resolve);
  const removeIds = remove.map(resolve);
  const both = addIds.filter(function (id) { return removeIds.indexOf(id) !== -1; });
  if (both.length) fail_('invalid_params', 'A label cannot be added and removed at the same time.');
  Gmail.Users.Messages.batchModify({ ids: params.message_ids, addLabelIds: addIds, removeLabelIds: removeIds }, 'me');
  return { modified: params.message_ids.length };
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

function eventOut_(ev) {
  const self = (ev.attendees || []).find(function (a) { return a.self; });
  return {
    id: ev.id,
    title: ev.summary || '(no title)',
    start: ev.start ? (ev.start.dateTime || ev.start.date) : null,
    end: ev.end ? (ev.end.dateTime || ev.end.date) : null,
    all_day: !!(ev.start && ev.start.date && !ev.start.dateTime),
    location: ev.location || '',
    description: ev.description || '',
    attendees: (ev.attendees || []).map(function (a) { return a.email; }),
    status: ev.status || 'confirmed',
    html_link: ev.htmlLink || null,
    organizer_self: !!(ev.organizer && ev.organizer.self) || !ev.organizer,
    transparency: ev.transparency || 'opaque',
    self_response: self ? self.responseStatus || null : null,
  };
}

function checkRange_(start, end, maxDays) {
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  if (e <= s) fail_('invalid_params', 'The end time must be after the start time.');
  if (maxDays && e - s > maxDays * 86400000) fail_('invalid_params', 'The time range can be at most ' + maxDays + ' days.');
}

function listEvents(params) {
  checkRange_(params.start, params.end, MAX_EVENT_SPAN_DAYS);
  const args = { timeMin: params.start, timeMax: params.end, singleEvents: true, orderBy: 'startTime', maxResults: params.max_results };
  if (params.query) args.q = params.query;
  const res = Calendar.Events.list('primary', args);
  return { events: (res.items || []).filter(function (ev) { return ev.status !== 'cancelled'; }).map(eventOut_) };
}

function getEvent(params) {
  return eventOut_(Calendar.Events.get('primary', params.event_id));
}

function createEvent(params) {
  checkRange_(params.start, params.end, 31);
  const resource = {
    summary: params.title,
    start: { dateTime: params.start },
    end: { dateTime: params.end },
  };
  if (params.description) resource.description = params.description;
  if (params.location) resource.location = params.location;
  const attendees = params.attendees || [];
  if (attendees.length) resource.attendees = attendees.map(function (email) { return { email: email }; });
  return eventOut_(Calendar.Events.insert(resource, 'primary', { sendUpdates: attendees.length ? 'all' : 'none' }));
}

function moveEvent(params) {
  checkRange_(params.new_start, params.new_end, 31);
  const current = Calendar.Events.get('primary', params.event_id);
  if (current.status === 'cancelled') fail_('not_found', 'That event was cancelled.');
  const patch = { start: { dateTime: params.new_start }, end: { dateTime: params.new_end } };
  const notify = (current.attendees || []).length ? 'all' : 'none';
  return eventOut_(Calendar.Events.patch(patch, 'primary', params.event_id, { sendUpdates: notify }));
}

function cancelEvent(params) {
  // Cancelling an event is the only removal this bridge supports. Emails and files are never deleted.
  Calendar.Events.get('primary', params.event_id);  // 404 → not_found before anything changes
  Calendar.Events.remove('primary', params.event_id, { sendUpdates: params.notify_attendees ? 'all' : 'none' });
  return { cancelled: true, event_id: params.event_id };
}

// ---------------------------------------------------------------------------
// Drive
// ---------------------------------------------------------------------------

/** Escape a value for use inside a single-quoted Drive query string. */
function driveQuote_(value) {
  return "'" + String(value).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
}

function fileOut_(f) {
  return {
    id: f.id,
    name: f.name,
    mime_type: f.mimeType,
    modified_time: f.modifiedTime || null,
    size: f.size !== undefined && f.size !== null ? Number(f.size) : null,
    web_view_link: f.webViewLink || null,
    parents: f.parents || [],
  };
}

function buildDriveQuery_(params) {
  const q = ['trashed = false'];
  if (params.name_contains) q.push('name contains ' + driveQuote_(params.name_contains));
  if (params.mime_type && params.mime_type !== 'any') q.push(DRIVE_MIME_TYPES[params.mime_type]);
  if (params.modified_after) q.push('modifiedTime > ' + driveQuote_(params.modified_after + 'T00:00:00'));
  if (params.folder_id) q.push(driveQuote_(params.folder_id) + ' in parents');
  return q.join(' and ');
}

function searchDriveFiles(params) {
  const res = Drive.Files.list({
    q: buildDriveQuery_(params), pageSize: params.max_results, orderBy: 'modifiedTime desc',
    fields: 'files(' + FILE_FIELDS + ')',
  });
  return { files: (res.files || []).map(fileOut_) };
}

function getFile(params) {
  return fileOut_(Drive.Files.get(params.file_id, { fields: FILE_FIELDS }));
}

function requireFolder_(folderId) {
  const f = Drive.Files.get(folderId, { fields: FILE_FIELDS + ',trashed' });
  if (f.mimeType !== FOLDER_MIME) fail_('invalid_params', "'" + f.name + "' is not a folder.");
  if (f.trashed) fail_('not_found', 'That folder is in the trash.');
  return f;
}

function createDriveFolder(params) {
  const resource = { name: params.name.trim(), mimeType: FOLDER_MIME };
  if (params.parent_folder_id) resource.parents = [requireFolder_(params.parent_folder_id).id];
  return fileOut_(Drive.Files.create(resource, null, { fields: FILE_FIELDS }));
}

function moveDriveFile(params) {
  if (params.file_id === params.folder_id) fail_('invalid_params', 'A folder cannot be moved into itself.');
  const folder = requireFolder_(params.folder_id);
  const file = Drive.Files.get(params.file_id, { fields: FILE_FIELDS });
  const parents = (file.parents || []).filter(function (p) { return p !== folder.id; });
  const args = { addParents: folder.id, fields: FILE_FIELDS };
  if (parents.length) args.removeParents = parents.join(',');
  return fileOut_(Drive.Files.update({}, params.file_id, null, args));
}

function findOrCreateFolder_(name) {
  const res = Drive.Files.list({
    q: 'name = ' + driveQuote_(name) + " and mimeType = '" + FOLDER_MIME + "' and trashed = false",
    pageSize: 1, fields: 'files(' + FILE_FIELDS + ')',
  });
  if (res.files && res.files.length) return res.files[0];
  return Drive.Files.create({ name: name, mimeType: FOLDER_MIME }, null, { fields: FILE_FIELDS });
}

function attachmentData_(messageId, part) {
  return part.body.attachmentId
    ? Gmail.Users.Messages.Attachments.get('me', messageId, part.body.attachmentId).data
    : part.body.data;
}

function saveAttachmentsToDrive(params) {
  const msg = Gmail.Users.Messages.get('me', params.message_id, { format: 'full' });
  const all = parsed_(msg).attachments;
  if (!all.length) fail_('not_found', 'That email has no attachments.');

  const wanted = params.attachment_ids && params.attachment_ids.length ? params.attachment_ids : null;
  const skipped = [];
  const chosen = [];
  if (wanted) {
    wanted.forEach(function (id) {
      const a = all.find(function (x) { return x.attachment_id === id; });
      if (a) chosen.push(a);
      else skipped.push({ filename: id, reason: 'not found on the email' });
    });
  } else {
    all.forEach(function (a) { chosen.push(a); });
  }

  const folder = params.folder_id ? requireFolder_(params.folder_id) : findOrCreateFolder_((params.folder_name || DEFAULT_FOLDER).trim());
  const saved = [];
  chosen.forEach(function (a) {
    if (a.size > MAX_ATTACHMENT_BYTES) {
      skipped.push({ filename: a.filename, reason: 'larger than the ' + (MAX_ATTACHMENT_BYTES / 1048576) + ' MB limit' });
      return;
    }
    const blob = Utilities.newBlob(Utilities.base64DecodeWebSafe(attachmentData_(params.message_id, a.part)),
      a.mime_type || 'application/octet-stream', a.filename);
    const f = Drive.Files.create({ name: a.filename, parents: [folder.id] }, blob, { fields: FILE_FIELDS });
    saved.push({ filename: a.filename, file_id: f.id, web_view_link: f.webViewLink || null });
  });
  return {
    folder: { id: folder.id, name: folder.name, web_view_link: folder.webViewLink || null },
    saved: saved,
    skipped: skipped,
  };
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const EMAIL_RE = /^[^\s@<>(),;:"\[\]\\]+@[^\s@<>(),;:"\[\]\\]+\.[^\s@<>(),;:"\[\]\\]+$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function bad_(key, why) { fail_('invalid_params', "'" + key + "' " + why); }

function checkString_(key, v, spec) {
  if (typeof v !== 'string') bad_(key, 'must be a string.');
  if (!spec.allowEmpty && !v.trim()) bad_(key, 'must not be empty.');
  if (v.length > (spec.max || 1000)) bad_(key, 'is too long (max ' + (spec.max || 1000) + ' characters).');
  if (spec.pattern && !spec.pattern.test(v)) bad_(key, 'has an invalid format.');
  return v;
}

/** Check params against an action spec. Returns a clean copy with defaults applied. */
function validate_(spec, params) {
  if (typeof params !== 'object' || Array.isArray(params)) fail_('invalid_params', 'params must be a JSON object.');
  Object.keys(params).forEach(function (k) {
    if (!Object.prototype.hasOwnProperty.call(spec, k)) bad_(k, 'is not a known parameter.');
  });
  const out = {};
  Object.keys(spec).forEach(function (key) {
    const s = spec[key];
    let v = params[key];
    if (v === undefined || v === null) {
      if (s.req) bad_(key, 'is required.');
      if (s.def !== undefined) out[key] = s.def;
      return;
    }
    switch (s.t) {
      case 'string':
        out[key] = checkString_(key, v, s);
        break;
      case 'int':
        if (typeof v !== 'number' || Math.floor(v) !== v) bad_(key, 'must be a whole number.');
        if (v < s.min || v > s.max) bad_(key, 'must be between ' + s.min + ' and ' + s.max + '.');
        out[key] = v;
        break;
      case 'bool':
        if (typeof v !== 'boolean') bad_(key, 'must be true or false.');
        out[key] = v;
        break;
      case 'enum':
        if (s.values.indexOf(v) === -1) bad_(key, 'must be one of: ' + s.values.join(', ') + '.');
        out[key] = v;
        break;
      case 'iso':
        if (typeof v !== 'string' || !ISO_RE.test(v) || isNaN(new Date(v).getTime())) {
          bad_(key, 'must be an ISO 8601 date-time with a timezone offset, e.g. 2025-01-31T15:00:00+05:30.');
        }
        out[key] = v;
        break;
      case 'date':
        if (typeof v !== 'string' || !DATE_RE.test(v) || isNaN(new Date(v + 'T00:00:00Z').getTime())) bad_(key, 'must be a date like 2025-01-31.');
        out[key] = v;
        break;
      case 'emails':
        if (!Array.isArray(v)) bad_(key, 'must be a list of email addresses.');
        if (s.req && !v.length) bad_(key, 'must contain at least one email address.');
        if (v.length > s.max) bad_(key, 'can have at most ' + s.max + ' addresses.');
        out[key] = v.map(function (a) {
          if (typeof a !== 'string' || !EMAIL_RE.test(a.trim())) bad_(key, 'contains an invalid email address: ' + String(a));
          return a.trim();
        });
        break;
      case 'strings':
        if (!Array.isArray(v)) bad_(key, 'must be a list.');
        if (v.length < (s.min || 0)) bad_(key, 'must contain at least ' + s.min + ' item(s).');
        if (v.length > s.max) bad_(key, 'can have at most ' + s.max + ' items.');
        out[key] = v.map(function (x) { return checkString_(key, x, s.item || {}); });
        break;
      default:
        fail_('internal', 'Unknown spec type for ' + key);
    }
  });
  return out;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function fail_(code, message) {
  const err = new Error(message);
  err.code = code;
  throw err;
}

function extractEmails_(s) {
  const found = String(s || '').match(/[^\s<>,;:"'()\[\]]+@[^\s<>,;:"'()\[\]]+/g) || [];
  const out = [];
  found.forEach(function (a) {
    a = a.toLowerCase();
    if (out.indexOf(a) === -1) out.push(a);
  });
  return out;
}

function safeEqual_(a, b) {
  // Constant-time comparison for strings of equal length.
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function errMessage_(err) {
  return String((err && err.message) || err).replace(/^API call to .* failed with error: /, '');
}

function toError_(err) {
  if (err && typeof err.code === 'string') return { code: err.code, message: err.message };
  const msg = errMessage_(err);
  if (/not found|requested entity was not found|deleted/i.test(msg)) return { code: 'not_found', message: 'That item was not found. It may have been deleted.' };
  if (/insufficient permission|forbidden|access denied|not authorized|permission/i.test(msg)) return { code: 'permission_denied', message: msg };
  if (/quota|rate limit|too many|limit exceeded/i.test(msg)) return { code: 'quota', message: msg };
  if (/invalid|bad request/i.test(msg)) return { code: 'invalid_params', message: msg };
  return { code: 'internal', message: msg };
}
