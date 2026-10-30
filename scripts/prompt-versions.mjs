#!/usr/bin/env node
// Dependency-free prompt source versioning; no inference or deployment actions.
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync, renameSync, unlinkSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const roles = ['common', 'coordinator', 'categorizer', 'investigator', 'analyzer', 'forecaster', 'advisor', 'risk', 'goals'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const versionPattern = /^v[1-9][0-9]*$/;
const fail = message => { throw new Error(message); };
const readJSON = path => JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
const json = data => JSON.stringify(data, null, 2) + '\n';
const checkVersion = value => versionPattern.test(value) || fail(`Invalid version: ${value}`);

export function catalog(root) {
  const directory = resolve(root, 'prompts/releases');
  if (!existsSync(directory)) fail('Missing releases directory');
  const names = readdirSync(directory).filter(name => /^v[1-9][0-9]*\.json$/.test(name));
  if (!names.length) fail('No release snapshots found');
  names.sort((a, b) => Number(a.slice(1, -5)) - Number(b.slice(1, -5)));
  const known = new Map();
  const snapshots = [];
  for (const name of names) {
    const bytes = readFileSync(resolve(directory, name));
    const release = readJSON(resolve(directory, name));
    if (`${release.release_version}.json` !== name) fail(`Release name mismatch: ${name}`);
    if (release.schema_version !== 1 || release.status !== 'draft' || release.evaluation_status !== 'not_run') fail(`Invalid source snapshot status: ${name}`);
    if (!release.reason?.trim() || !release.created_at) fail(`Missing provenance: ${name}`);
    const previous = snapshots.at(-1);
    if (release.parent_release !== (previous?.release.release_version ?? null) || release.parent_sha256 !== (previous?.sha256 ?? null)) fail(`Broken history chain: ${name}`);
    if (Object.keys(release.templates ?? {}).sort().join() !== [...roles].sort().join()) fail(`Expected all nine templates: ${name}`);
    for (const role of roles) {
      const entry = release.templates[role];
      checkVersion(entry.version);
      if (entry.path !== `${role}/${entry.version}.md`) fail(`Invalid template path: ${name}/${role}`);
      if (!/^[a-f0-9]{64}$/.test(entry.sha256)) fail(`Invalid checksum: ${name}/${role}`);
      const actual = hash(readFileSync(resolve(root, 'prompts', entry.path)));
      if (actual !== entry.sha256) fail(`Template changed: ${entry.path}. Restore it and create a new version.`);
      const key = `${role}/${entry.version}`;
      if (known.has(key) && known.get(key) !== entry.sha256) fail(`Historical version changed: ${key}`);
      known.set(key, entry.sha256);
    }
    snapshots.push({ release, sha256: hash(bytes) });
  }
  return snapshots;
}

export function validate(root) {
  const snapshots = catalog(root);
  const pointer = readJSON(resolve(root, 'prompts/manifest.json'));
  if (pointer.schema_version !== 1) fail('Unsupported manifest schema');
  checkVersion(pointer.selected_release);
  const selected = snapshots.find(item => item.release.release_version === pointer.selected_release);
  if (!selected || pointer.release_path !== `releases/${pointer.selected_release}.json` || pointer.release_sha256 !== selected.sha256) fail('Selected release checksum/path mismatch');
  return { snapshots, selected };
}

function selectPointer(root, snapshot) {
  const destination = resolve(root, 'prompts/manifest.json');
  const temporary = `${destination}.${process.pid}.tmp`;
  try {
    writeFileSync(temporary, json({
      schema_version: 1,
      selected_release: snapshot.release.release_version,
      release_path: `releases/${snapshot.release.release_version}.json`,
      release_sha256: snapshot.sha256,
    }), { flag: 'wx' });
    renameSync(temporary, destination);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

export function select(root, version) {
  checkVersion(version);
  const { snapshots } = validate(root);
  const snapshot = snapshots.find(item => item.release.release_version === version);
  if (!snapshot) fail(`Unknown release: ${version}`);
  selectPointer(root, snapshot);
  return version;
}

export function snapshot(root, selections, reason, createdAt = new Date().toISOString()) {
  if (!reason?.trim()) fail('A nonempty change reason is required');
  const { snapshots, selected } = validate(root);
  const templates = structuredClone(selected.release.templates);
  if (!Object.keys(selections).length) fail('Select at least one changed template');
  for (const [role, version] of Object.entries(selections)) {
    if (!roles.includes(role)) fail(`Unknown role: ${role}`);
    checkVersion(version);
    if (snapshots.some(item => item.release.templates[role].version === version)) fail(`Version already recorded: ${role}/${version}. Create a new version.`);
    const maxVersion = Math.max(...snapshots.map(item => Number(item.release.templates[role].version.slice(1))));
    if (Number(version.slice(1)) <= maxVersion) fail(`Version must increase for ${role}`);
    const path = `${role}/${version}.md`;
    const bytes = readFileSync(resolve(root, 'prompts', path));
    if (!bytes.toString('utf8').trim()) fail(`Empty template: ${path}`);
    templates[role] = { version, path, sha256: hash(bytes) };
  }
  const previous = snapshots.at(-1);
  const version = `v${Number(previous.release.release_version.slice(1)) + 1}`;
  const release = {
    schema_version: 1, release_version: version, status: 'draft', evaluation_status: 'not_run',
    created_at: createdAt, reason: reason.trim(),
    parent_release: previous.release.release_version, parent_sha256: previous.sha256,
    based_on_release: selected.release.release_version, templates,
  };
  const bytes = json(release);
  mkdirSync(resolve(root, 'prompts/releases'), { recursive: true });
  writeFileSync(resolve(root, `prompts/releases/${version}.json`), bytes, { flag: 'wx' });
  selectPointer(root, { release, sha256: hash(bytes) });
  return version;
}

export function main(args, root = resolve(dirname(fileURLToPath(import.meta.url)), '..')) {
  const [command, ...rest] = args;
  if (command === 'validate' && !rest.length) {
    const { snapshots, selected } = validate(root);
    console.log(`Validated ${snapshots.length} release snapshot(s), nine templates each; selected ${selected.release.release_version}. Evaluation: NOT RUN.`);
  } else if (command === 'list' && !rest.length) {
    const { snapshots, selected } = validate(root);
    for (const item of snapshots) console.log(`${item.release.release_version === selected.release.release_version ? '*' : ' '} ${item.release.release_version} | ${item.release.created_at} | ${item.release.reason}`);
  } else if (command === 'select' && rest.length === 1) {
    console.log(`Selected source snapshot ${select(root, rest[0])}. No deployment approval granted.`);
  } else if (command === 'snapshot') {
    const selections = {};
    let reason;
    for (let i = 0; i < rest.length; i++) {
      if (rest[i] === '--reason' && reason === undefined && rest[i + 1]) reason = rest[++i];
      else if (rest[i] === '--set' && rest[i + 1]) {
        const match = /^([a-z]+)=(v[1-9][0-9]*)$/.exec(rest[++i]);
        if (!match || selections[match[1]]) fail('Expected a unique --set role=vN');
        selections[match[1]] = match[2];
      } else fail(`Unknown or incomplete option: ${rest[i]}`);
    }
    console.log(`Recorded and selected ${snapshot(root, selections, reason)}. Evaluation: NOT RUN.`);
  } else fail('Usage: node scripts/prompt-versions.mjs validate | list | select vN | snapshot --set role=vN [--set role=vN] --reason "change reason"');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); }
  catch (error) { console.error(`Prompt version error: ${error.message}`); process.exitCode = 1; }
}