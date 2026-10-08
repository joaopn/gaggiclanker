// Runs GaggiMate firmware v1.9.0's own profile-curve code over profile documents.
//
//   node harness.mjs <ExtendedProfileChart.jsx> <profiles.json>
//
// The functions are not exported and the file imports preact and chart.js, so the harness does
// not import it: it cuts the firmware's own source text out of the file (the easing functions,
// `applyEasing` and `prepareData`, from `function easeLinear` up to `function makeChartData`;
// `buildPhaseRanges`; and the loop in `makeChartData` that sums the phase durations) and
// evaluates exactly that text, unchanged, in a function of its own. Nothing is rewritten and
// nothing is copied into the repository. If the file's layout changes so a piece cannot be
// found, it stops rather than guessing.
import { readFileSync } from 'node:fs';

const [source, profilesPath] = process.argv.slice(2);
const text = readFileSync(source, 'utf8');

function between(start, end, from = 0) {
  const a = text.indexOf(start, from);
  const b = text.indexOf(end, a);
  if (a < 0 || b < 0) throw new Error(`firmware source layout changed: ${start} .. ${end}`);
  return text.slice(a, b);
}

const interval = text.match(/^const POINT_INTERVAL = [^;]+;/m)?.[0];
const prepare = between('function easeLinear', 'function makeChartData');
const ranges = between('function buildPhaseRanges', 'function getPhaseIndexForX');
const sum = between('let duration = 0;', 'const chartData');
if (!interval) throw new Error('firmware source layout changed: POINT_INTERVAL');

const factory = new Function(
  `${interval}\n${prepare}\n${ranges}\n` +
    `return { prepareData, buildPhaseRanges, totalDuration: (phases) => { ${sum} return duration; } };`,
);
const firmware = factory();

const cases = JSON.parse(readFileSync(profilesPath, 'utf8'));
const out = {};
for (const [name, profile] of Object.entries(cases)) {
  const phases = Array.isArray(profile?.phases) ? profile.phases : [];
  const pack = points => points.map(p => [p.x, p.y, p.target ? 1 : 0]);
  out[name] = {
    pressure: pack(firmware.prepareData(phases, 'pressure')),
    flow: pack(firmware.prepareData(phases, 'flow')),
    ranges: firmware.buildPhaseRanges(phases),
    xMax: firmware.totalDuration(phases),
  };
}
console.log(JSON.stringify(out));
