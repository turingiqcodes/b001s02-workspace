'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createGas } = require('./helpers/gas');

const FOLDER = 'application/vnd.google-apps.folder';

test('search_drive_files builds an escaped structured query', () => {
  const gas = createGas({ driveListResult: [{ id: 'f1', name: 'invoice.pdf', mimeType: 'application/pdf', size: '123', parents: ['root'] }] });
  const data = gas.ok('search_drive_files', { name_contains: "Bob's \\ invoice' or trashed = true or name contains '", mime_type: 'pdf', modified_after: '2025-01-01', folder_id: 'abc_123', max_results: 5 });
  const args = gas.calls('Files.list')[0].args;
  assert.equal(args.q, "trashed = false and name contains 'Bob\\'s \\\\ invoice\\' or trashed = true or name contains \\'' and mimeType = 'application/pdf' and modifiedTime > '2025-01-01T00:00:00' and 'abc_123' in parents");
  assert.equal(args.pageSize, 5);
  assert.deepEqual(data.files[0], { id: 'f1', name: 'invoice.pdf', mime_type: 'application/pdf', modified_time: null, size: 123, web_view_link: null, parents: ['root'] });
  assert.equal(gas.post('search_drive_files', { folder_id: "x' or '1'='1" }).error.code, 'invalid_params');
});

test('image filter and default query', () => {
  const gas = createGas();
  gas.ok('search_drive_files', { mime_type: 'image' });
  gas.ok('search_drive_files', {});
  const [a, b] = gas.calls('Files.list').map((c) => c.args.q);
  assert.equal(a, "trashed = false and mimeType contains 'image/'");
  assert.equal(b, 'trashed = false');
});

test('create_drive_folder and move_drive_file require real folders', () => {
  const gas = createGas();
  gas.state.files.doc1 = { id: 'doc1', name: 'Notes', mimeType: 'application/vnd.google-apps.document', parents: ['root'] };
  const folder = gas.ok('create_drive_folder', { name: 'Invoices 2025' });
  assert.equal(folder.mime_type, FOLDER);
  assert.match(gas.post('create_drive_folder', { name: 'Sub', parent_folder_id: 'doc1' }).error.message, /is not a folder/);
  const moved = gas.ok('move_drive_file', { file_id: 'doc1', folder_id: folder.id });
  assert.deepEqual(moved.parents, [folder.id]);
  assert.deepEqual(gas.calls('Files.update')[0].args.args, { addParents: folder.id, fields: 'id,name,mimeType,modifiedTime,size,webViewLink,parents', removeParents: 'root' });
  assert.match(gas.post('move_drive_file', { file_id: folder.id, folder_id: 'doc1' }).error.message, /is not a folder/);
});

test('save_attachments_to_drive saves to a named folder and skips oversized or unknown attachments', () => {
  const gas = createGas();
  gas.addMessage({ id: 'm1', attachments: [
    { filename: 'invoice.pdf', size: 5000 },
    { filename: 'video.mov', size: 21 * 1024 * 1024, mimeType: 'video/quicktime' },
    { filename: 'receipt.png', size: 300, mimeType: 'image/png' }] });
  const data = gas.ok('save_attachments_to_drive', { message_id: 'm1', attachment_ids: ['1', '2', '9'] });
  assert.equal(data.folder.name, 'Email Attachments');
  assert.deepEqual(data.saved.map((s) => s.filename), ['invoice.pdf']);
  assert.deepEqual(data.skipped.map((s) => s.filename), ['9', 'video.mov']);
  assert.match(data.skipped[1].reason, /20 MB/);
  const creates = gas.calls('Files.create');
  assert.equal(creates[0].args.res.mimeType, FOLDER, 'folder created first because none existed');
  assert.deepEqual(creates[1].args.res, { name: 'invoice.pdf', parents: [data.folder.id] });
  assert.equal(creates[1].args.blob.text, 'file-bytes-att_m1_1');

  const all = gas.ok('save_attachments_to_drive', { message_id: 'm1', folder_id: data.folder.id });
  assert.deepEqual(all.saved.map((s) => s.filename), ['invoice.pdf', 'receipt.png']);

  gas.addMessage({ id: 'm2' });
  assert.equal(gas.post('save_attachments_to_drive', { message_id: 'm2' }).error.code, 'not_found');
});
