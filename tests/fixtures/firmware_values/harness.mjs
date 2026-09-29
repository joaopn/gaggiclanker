// Runs GaggiMate firmware v1.9.0's own shot analyzer code (puckResistance.js,
// waterIntegration.js) over sample lists and prints what it computes.
//
//   node harness.mjs <analyzer dir> <samples.json>
//
// The analyzer's files import each other without an extension, which Node's ESM
// loader refuses, so they are copied into a scratch directory with that one
// specifier patched; nothing else of them is touched and nothing is copied into
// the repository. Phases are grouped by `phaseNumber` exactly as shotAnalysis.js
// groups them, and the whole shot is every sample, as it passes `shotData.samples`.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [analyzerDir, samplesPath] = process.argv.slice(2);
const scratch = mkdtempSync(join(tmpdir(), 'analyzer-'));
for (const name of ['puckResistance.js', 'metricStats.js', 'waterIntegration.js']) {
  const source = readFileSync(join(analyzerDir, name), 'utf8').replace(
    /from '\.\/(\w+)'/g,
    "from './$1.js'",
  );
  writeFileSync(join(scratch, name), source);
}
const { getNativePuckResistanceStats, getLiquidResistanceStats } = await import(
  pathToFileURL(join(scratch, 'puckResistance.js')).href
);
const { calculatePumpedWater, hasCompleteRecordedPumpedWater } = await import(
  pathToFileURL(join(scratch, 'waterIntegration.js')).href
);

const stats = samples => ({
  pr: getNativePuckResistanceStats(samples),
  lr: getLiquidResistanceStats(samples),
});

const shots = JSON.parse(readFileSync(samplesPath, 'utf8'));
const out = {};
for (const [name, samples] of Object.entries(shots)) {
  const phases = {};
  for (const sample of samples) (phases[sample.phaseNumber] ??= []).push(sample);
  out[name] = {
    whole: stats(samples),
    phases: Object.fromEntries(Object.entries(phases).map(([n, group]) => [n, stats(group)])),
    // Only where the analyzer itself uses the recorded counter; elsewhere it
    // integrates flow, which is an estimate this project does not report.
    water_pumped_ml: hasCompleteRecordedPumpedWater(samples) ? calculatePumpedWater(samples) : null,
  };
}
console.log(JSON.stringify(out));
