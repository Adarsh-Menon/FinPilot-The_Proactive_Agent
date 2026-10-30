import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, cpSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validate, snapshot, select } from '../scripts/prompt-versions.mjs';
const source = fileURLToPath(new URL('../prompts/', import.meta.url));
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'finpilot-prompts-'));
  cpSync(source, join(root, 'prompts'), { recursive: true });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}
function next(root, role) {
  const templates = validate(root).snapshots;
  const version = `v${Math.max(...templates.map(s => Number(s.release.templates[role].version.slice(1)))) + 1}`;
  writeFileSync(join(root, `prompts/${role}/${version}.md`), `# ${role} ${version}\nChanged instruction for isolated test.\n`);
  return version;
}
test('existing catalog validates and source selection can roll back', t => {
  const root = fixture(t);
  const original = validate(root).selected.release.release_version;
  const version = next(root, 'advisor');
  const created = snapshot(root, {advisor: version}, 'Test change', '2026-10-18');
  assert.equal(validate(root).selected.release.templates.advisor.version, version);
  select(root, original);
  assert.equal(validate(root).selected.release.release_version, original);
  assert.ok(validate(root).snapshots.some(s => s.release.release_version === created));
});
test('editing a recorded template is detected even after selecting a newer release', t => {
  const root = fixture(t);
  const historical = validate(root).selected.release.templates.advisor.path;
  snapshot(root, {advisor: next(root, 'advisor')}, 'Test change');
  writeFileSync(join(root, 'prompts', historical), 'tampered');
  assert.throws(() => validate(root), /Template changed/);
});
test('editing a historical manifest breaks the release hash chain', t => {
  const root = fixture(t);
  const old = validate(root).snapshots.at(-1).release.release_version;
  snapshot(root, {advisor: next(root, 'advisor')}, 'Test change');
  const path = join(root, `prompts/releases/${old}.json`);
  const release = JSON.parse(readFileSync(path, 'utf8'));
  release.reason = 'Changed history';
  writeFileSync(path, JSON.stringify(release));
  assert.throws(() => validate(root), /Broken history chain/);
});
test('common prompt changes preserve role versions but invalidate the bundle', t => {
  const root = fixture(t);
  const before = validate(root).selected;
  snapshot(root, {common: next(root, 'common')}, 'Shared rule update');
  const after = validate(root).selected;
  assert.notEqual(before.sha256, after.sha256);
  assert.deepEqual(before.release.templates.advisor, after.release.templates.advisor);
  assert.notEqual(before.release.templates.common.sha256, after.release.templates.common.sha256);
});
test('invalid versions, reused versions, unknown roles and missing reasons fail', t => {
  const root = fixture(t);
  const current = validate(root).selected.release.templates.advisor.version;
  assert.throws(() => select(root, '../v1'), /Invalid version/);
  assert.throws(() => snapshot(root, {advisor: current}, 'Reuse'), /already recorded/);
  assert.throws(() => snapshot(root, {unknown: 'v2'}, 'Unknown'), /Unknown role/);
  assert.throws(() => snapshot(root, {advisor: 'v2'}, ''), /reason/);
});
test('selected release tampering fails closed', t => {
  const root = fixture(t);
  const path = join(root, 'prompts/manifest.json');
  const pointer = JSON.parse(readFileSync(path, 'utf8'));
  pointer.release_sha256 = '0'.repeat(64);
  writeFileSync(path, JSON.stringify(pointer));
  assert.throws(() => validate(root), /checksum\/path mismatch/);
});