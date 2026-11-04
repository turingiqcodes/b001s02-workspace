/**
 * Email Manager Agent — Google Apps Script bridge.
 *
 * The FastAPI backend POSTs {token, action, params} to this web app.
 * Responses are {ok: true, data} or {ok: false, error: {code, message}}.
 *
 * Uses the Gmail, Calendar and Drive *advanced services* so the project can run
 * with the narrow scopes in appsscript.json (gmail.modify, calendar.events,
 * drive.file) instead of full-account access.
 *
 * Setup: Project Settings > Script Properties > add BRIDGE_TOKEN (same value as
 * backend/.env). Then Deploy > New deployment > Web app,
 * Execute as: Me, Who has access: Anyone.
 */

const MAX_BODY_CHARS = 50000;
const DEFAULT_FOLDER = 'Email Attachments';
const META_HEADERS = ['From', 'To', 'Cc', 'Subject', 'Date'];

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

function doGet() {
  return ContentService.createTextOutput('Email Manager bridge is running. Use POST.');
}

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: { code: 'BAD_REQUEST', message: 'Request body must be JSON.' } });
  }

  const expected = PropertiesService.getScriptProperties().getProperty('BRIDGE_TOKEN');
  if (!expected || typeof req.token !== 'string' || !safeEqual_(req.token, expected)) {
    return json_({ ok: false, error: { code: 'UNAUTHORIZED', message: 'Invalid bridge token.' } });
  }

  const handler = ACTIONS[req.action];
  if (!handler) {
    return json_({ ok: false, error: { code: 'UNKNOWN_ACTION', message: 'Unknown action: ' + req.action } });
  }

  try {
    return json_({ ok: true, data: handler(req.params || {}) });
  } catch (err) {
    console.error(req.action, err && err.stack ? err.stack : err);
    return json_({ ok: false, error: toError_(err) });
  }
}

const ACTIONS = {
  get_context: getContext,
  search_emails: searchEmails,
  read_email: readEmail,
  list_labels: listLabels,
  list_drafts: listDrafts,
  get_draft: getDraft,
  create_draft: createDraft,
  update_draft: updateDraft,
  send_draft: sendDraft,
  send_email: sendEmail,
  create_label: createLabel,
  modify_labels: modifyLabels,
  list_events: listEvents,
  check_availability: checkAvailability,
  get_event: getEvent,
  create_event: createEvent,
  update_event: updateEvent,
  cancel_event: cancelEvent,
  save_attachment_to_drive: saveAttachmentToDrive,
};

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

function getContext() {
  const profile = Gmail.Users.getProfile('me');
  const cal = Calendar.Events.list('primary', { maxResults: 1 });
  return { email: profile.emailAddress, timezone: cal.timeZone || Session.getScriptTimeZone() };
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
  const h = (payload.headers || []).find(function (x) { return x.name.toLowerCase() === name.toLowerCase(); });
  return h ? h.value : '';
}

function summary_(msg, labels) {
  const p = msg.payload || {};
  const ids = msg.labelIds || [];
  return {
    id: msg.id,
    threadId: msg.threadId,
    from: header_(p, 'From'),
    to: header_(p, 'To'),
    cc: header_(p, 'Cc'),
    subject: header_(p, 'Subject'),
    date: header_(p, 'Date'),
    snippet: msg.snippet || '',
    labels: ids.map(function (id) { return labels[id] ? labels[id].name : id; }),
    unread: ids.indexOf('UNREAD') !== -1,
  };
}

function searchEmails(params) {
  const max = clamp_(params.max_results || 10, 1, 50);
  const list = Gmail.Users.Messages.list('me', { q: params.query || '', maxResults: max });
  const labels = labelMap_();
  const messages = (list.messages || []).map(function (m) {
    const msg = Gmail.Users.Messages.get('me', m.id, { format: 'metadata', metadataHeaders: META_HEADERS });
    return summary_(msg, labels);
  });
  return { messages: messages, total: list.resultSizeEstimate || messages.length };
}

function decode_(data) {
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(data)).getDataAsString('UTF-8');
}

function walkParts_(part, out) {
  const mime = part.mimeType || '';
  if (part.filename && part.body && (part.body.attachmentId || part.body.data)) {
    out.attachments.push({
      // partId is stable across fetches; Gmail's attachmentId is not.
      attachmentId: part.partId,
      filename: part.filename,
      mimeType: mime,
      size: part.body.size || 0,
    });
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
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function readEmail(params) {
  const msg = Gmail.Users.Messages.get('me', required_(params, 'message_id'), { format: 'full' });
  const out = { plain: '', html: '', attachments: [] };
  walkParts_(msg.payload, out);
  let body = out.plain || stripHtml_(out.html || '');
  let truncated = false;
  if (body.length > MAX_BODY_CHARS) {
    const omitted = body.length - MAX_BODY_CHARS;
    body = body.slice(0, MAX_BODY_CHARS) + '\n\n[Body truncated: ' + omitted + ' more characters not shown]';
    truncated = true;
  }
  const result = summary_(msg, labelMap_());
  result.body = body;
  result.bodyTruncated = truncated;
  result.attachments = out.attachments;
  result.hasAttachments = out.attachments.length > 0;
  return result;
}

function listLabels() {
  return {
    labels: (Gmail.Users.Labels.list('me').labels || []).map(function (l) {
      return { id: l.id, name: l.name, type: l.type };
    }),
  };
}

function draftOut_(draft) {
  const msg = draft.message || {};
  const p = msg.payload || {};
  const out = { plain: '', html: '', attachments: [] };
  if (p.mimeType) walkParts_(p, out);
  return {
    id: draft.id,
    to: splitAddrs_(header_(p, 'To')),
    cc: splitAddrs_(header_(p, 'Cc')),
    subject: header_(p, 'Subject'),
    body: out.plain || stripHtml_(out.html || ''),
    threadId: msg.threadId || null,
    inReplyTo: header_(p, 'In-Reply-To') || null,
    references: header_(p, 'References') || null,
  };
}

function listDrafts(params) {
  const max = clamp_(params.max_results || 10, 1, 50);
  const list = Gmail.Users.Drafts.list('me', { maxResults: max });
  return {
    drafts: (list.drafts || []).map(function (d) {
      return draftOut_(Gmail.Users.Drafts.get('me', d.id, { format: 'full' }));
    }),
  };
}

function getDraft(params) {
  return draftOut_(Gmail.Users.Drafts.get('me', required_(params, 'draft_id'), { format: 'full' }));
}

// ---------------------------------------------------------------------------
// Gmail — write
// ---------------------------------------------------------------------------

function encodeHeader_(value) {
  return /^[\x20-\x7e]*$/.test(value)
    ? value
    : '=?UTF-8?B?' + Utilities.base64Encode(value, Utilities.Charset.UTF_8) + '?=';
}

function buildRaw_(m) {
  const lines = [];
  lines.push('To: ' + (m.to || []).join(', '));
  if (m.cc && m.cc.length) lines.push('Cc: ' + m.cc.join(', '));
  lines.push('Subject: ' + encodeHeader_(m.subject || ''));
  if (m.inReplyTo) lines.push('In-Reply-To: ' + m.inReplyTo);
  if (m.references) lines.push('References: ' + m.references);
  lines.push('MIME-Version: 1.0');
  lines.push('Content-Type: text/plain; charset=UTF-8');
  lines.push('Content-Transfer-Encoding: base64');
  lines.push('');
  lines.push(Utilities.base64Encode(m.body || '', Utilities.Charset.UTF_8));
  return Utilities.base64EncodeWebSafe(lines.join('\r\n'), Utilities.Charset.UTF_8);
}

function replyInfo_(messageId) {
  if (!messageId) return {};
  const msg = Gmail.Users.Messages.get('me', messageId, {
    format: 'metadata', metadataHeaders: ['Message-ID', 'References', 'Subject'],
  });
  const msgId = header_(msg.payload, 'Message-ID');
  const refs = header_(msg.payload, 'References');
  return {
    threadId: msg.threadId,
    inReplyTo: msgId || null,
    references: [refs, msgId].filter(String).join(' ') || null,
  };
}

function createDraft(params) {
  const reply = replyInfo_(params.reply_to_message_id);
  const raw = buildRaw_({
    to: params.to, cc: params.cc, subject: params.subject, body: params.body,
    inReplyTo: reply.inReplyTo, references: reply.references,
  });
  const message = { raw: raw };
  if (reply.threadId) message.threadId = reply.threadId;
  const draft = Gmail.Users.Drafts.create({ message: message }, 'me');
  return getDraft({ draft_id: draft.id });
}

function updateDraft(params) {
  const id = required_(params, 'draft_id');
  const cur = getDraft({ draft_id: id });
  const merged = {
    to: params.to !== undefined ? params.to : cur.to,
    cc: params.cc !== undefined ? params.cc : cur.cc,
    subject: params.subject !== undefined ? params.subject : cur.subject,
    body: params.body !== undefined ? params.body : cur.body,
    inReplyTo: cur.inReplyTo,
    references: cur.references,
  };
  const message = { raw: buildRaw_(merged) };
  if (cur.threadId) message.threadId = cur.threadId;
  Gmail.Users.Drafts.update({ id: id, message: message }, 'me', id);
  return getDraft({ draft_id: id });
}

function sendDraft(params) {
  const res = Gmail.Users.Drafts.send({ id: required_(params, 'draft_id') }, 'me');
  return { messageId: res.id, threadId: res.threadId };
}

function sendEmail(params) {
  const reply = replyInfo_(params.reply_to_message_id);
  const message = {
    raw: buildRaw_({
      to: params.to, cc: params.cc, subject: params.subject, body: params.body,
      inReplyTo: reply.inReplyTo, references: reply.references,
    }),
  };
  if (reply.threadId) message.threadId = reply.threadId;
  const res = Gmail.Users.Messages.send(message, 'me');
  return { messageId: res.id, threadId: res.threadId };
}

function createLabel(params) {
  const name = required_(params, 'name');
  const exists = (Gmail.Users.Labels.list('me').labels || []).some(function (l) {
    return l.name.toLowerCase() === name.toLowerCase();
  });
  if (exists) fail_('ALREADY_EXISTS', "A label named '" + name + "' already exists.");
  const l = Gmail.Users.Labels.create(
    { name: name, labelListVisibility: 'labelShow', messageListVisibility: 'show' }, 'me');
  return { id: l.id, name: l.name, type: l.type };
}

function modifyLabels(params) {
  const ids = params.message_ids || [];
  if (!ids.length) fail_('INVALID_ARGUMENT', 'No emails were given.');
  if (ids.length > 1000) fail_('INVALID_ARGUMENT', 'At most 1000 emails can be changed at once.');
  const all = Gmail.Users.Labels.list('me').labels || [];
  function resolve(name) {
    const l = all.find(function (x) { return x.name.toLowerCase() === String(name).toLowerCase(); });
    if (!l) fail_('NOT_FOUND', "Label '" + name + "' does not exist.");
    if (l.type !== 'user') fail_('FORBIDDEN_LABEL', "'" + name + "' is a system label and can't be changed here.");
    return l.id;
  }
  const add = (params.add_labels || []).map(resolve);
  const remove = (params.remove_labels || []).map(resolve);
  if (!add.length && !remove.length) fail_('INVALID_ARGUMENT', 'No labels to add or remove.');
  Gmail.Users.Messages.batchModify({ ids: ids, addLabelIds: add, removeLabelIds: remove }, 'me');
  return { modified: ids.length };
}

// ---------------------------------------------------------------------------
// Calendar
// ---------------------------------------------------------------------------

function eventOut_(ev) {
  return {
    id: ev.id,
    summary: ev.summary || '(no title)',
    start: ev.start ? (ev.start.dateTime || ev.start.date) : null,
    end: ev.end ? (ev.end.dateTime || ev.end.date) : null,
    allDay: !!(ev.start && ev.start.date),
    attendees: (ev.attendees || []).map(function (a) { return a.email; }),
    description: ev.description || '',
    location: ev.location || '',
    htmlLink: ev.htmlLink || null,
    transparency: ev.transparency || 'opaque',
  };
}

function eventsBetween_(timeMin, timeMax, max) {
  const res = Calendar.Events.list('primary', {
    timeMin: timeMin, timeMax: timeMax, singleEvents: true, orderBy: 'startTime', maxResults: max,
  });
  return res.items || [];
}

function listEvents(params) {
  const items = eventsBetween_(required_(params, 'time_min'), required_(params, 'time_max'),
    clamp_(params.max_results || 25, 1, 100));
  return { events: items.map(eventOut_) };
}

function checkAvailability(params) {
  const timeMin = required_(params, 'time_min');
  const timeMax = required_(params, 'time_max');
  const busy = eventsBetween_(timeMin, timeMax, 250)
    .filter(function (ev) {
      if (ev.transparency === 'transparent') return false;
      const self = (ev.attendees || []).find(function (a) { return a.self; });
      return !(self && self.responseStatus === 'declined');
    })
    .map(function (ev) {
      const o = eventOut_(ev);
      return { start: o.start, end: o.end, summary: o.summary };
    });
  return { timeMin: timeMin, timeMax: timeMax, busy: busy, isFree: busy.length === 0 };
}

function getEvent(params) {
  return eventOut_(Calendar.Events.get('primary', required_(params, 'event_id')));
}

function sendUpdates_(params) {
  return params.send_updates === false ? 'none' : 'all';
}

function createEvent(params) {
  if (new Date(params.end) <= new Date(params.start)) fail_('INVALID_ARGUMENT', 'The meeting must end after it starts.');
  const resource = {
    summary: required_(params, 'summary'),
    description: params.description || '',
    location: params.location || '',
    start: { dateTime: required_(params, 'start') },
    end: { dateTime: required_(params, 'end') },
    attendees: (params.attendees || []).map(function (email) { return { email: email }; }),
  };
  return eventOut_(Calendar.Events.insert(resource, 'primary', { sendUpdates: sendUpdates_(params) }));
}

function updateEvent(params) {
  const id = required_(params, 'event_id');
  const patch = {};
  if (params.summary !== undefined) patch.summary = params.summary;
  if (params.description !== undefined) patch.description = params.description;
  if (params.location !== undefined) patch.location = params.location;
  if (params.start !== undefined) patch.start = { dateTime: params.start };
  if (params.end !== undefined) patch.end = { dateTime: params.end };
  if (params.attendees !== undefined) patch.attendees = params.attendees.map(function (e) { return { email: e }; });
  return eventOut_(Calendar.Events.patch(patch, 'primary', id, { sendUpdates: sendUpdates_(params) }));
}

function cancelEvent(params) {
  const id = required_(params, 'event_id');
  Calendar.Events.remove('primary', id, { sendUpdates: sendUpdates_(params) });
  return { cancelled: id };
}

// ---------------------------------------------------------------------------
// Drive
// ---------------------------------------------------------------------------

function findPart_(part, partId) {
  if (part.partId === partId) return part;
  const children = part.parts || [];
  for (let i = 0; i < children.length; i++) {
    const hit = findPart_(children[i], partId);
    if (hit) return hit;
  }
  return null;
}

function folderId_(name) {
  const safe = name.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  const res = Drive.Files.list({
    q: "name = '" + safe + "' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
    fields: 'files(id)', pageSize: 1,
  });
  if (res.files && res.files.length) return res.files[0].id;
  return Drive.Files.create({ name: name, mimeType: 'application/vnd.google-apps.folder' }).id;
}

function saveAttachmentToDrive(params) {
  const messageId = required_(params, 'message_id');
  const msg = Gmail.Users.Messages.get('me', messageId, { format: 'full' });
  const part = findPart_(msg.payload, required_(params, 'attachment_id'));
  if (!part || !part.filename) fail_('NOT_FOUND', 'That attachment was not found on the email.');

  const data = part.body.attachmentId
    ? Gmail.Users.Messages.Attachments.get('me', messageId, part.body.attachmentId).data
    : part.body.data;
  const blob = Utilities.newBlob(Utilities.base64DecodeWebSafe(data), part.mimeType || 'application/octet-stream', part.filename);
  const folderName = params.folder_name || DEFAULT_FOLDER;
  const file = Drive.Files.create(
    { name: part.filename, parents: [folderId_(folderName)] }, blob, { fields: 'id,name,webViewLink' });
  return { id: file.id, name: file.name, folder: folderName, webViewLink: file.webViewLink };
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

function required_(params, key) {
  const v = params[key];
  if (v === undefined || v === null || v === '') fail_('INVALID_ARGUMENT', 'Missing ' + key + '.');
  return v;
}

function clamp_(n, lo, hi) {
  n = Number(n) || lo;
  return Math.max(lo, Math.min(hi, Math.floor(n)));
}

function splitAddrs_(s) {
  return s ? s.split(',').map(function (x) { return x.trim(); }).filter(String) : [];
}

function safeEqual_(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function toError_(err) {
  if (err && err.code && typeof err.code === 'string') return { code: err.code, message: err.message };
  const msg = String((err && err.message) || err);
  if (/not found|requested entity was not found/i.test(msg)) return { code: 'NOT_FOUND', message: 'That item was not found. It may have been deleted.' };
  if (/insufficient permission|forbidden|access denied|not authorized/i.test(msg)) return { code: 'PERMISSION_DENIED', message: msg };
  if (/quota|rate limit|too many/i.test(msg)) return { code: 'QUOTA', message: msg };
  if (/invalid|bad request/i.test(msg)) return { code: 'INVALID_ARGUMENT', message: msg.replace(/^API call to .* failed with error: /, '') };
  return { code: 'UNKNOWN', message: msg.replace(/^API call to .* failed with error: /, '') };
}
