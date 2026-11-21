// The walkthrough as a load scenario (S07-01): what a planner does all day, against the edge, so it
// runs unchanged against whichever side is active. Every request is named; the summary gives each
// name its own figures, so the two sides can be compared request by request.
//
// One iteration, one virtual user (VU):
//   plan        POST a shipment whose road leg is given by its ends: transportation asks routing
//               for the distance (flow 1: a method call in the modulith, HTTP between the services)
//   shipment    GET it back by id
//   pickup      POST, then departure, then
//   deliver     POST: the outbox sends ShipmentDelivered; the warehouse at the destination opens an
//               arrival (flow 2: in-process, or through Kafka), and the analytics loader stores it
//   warehouses  GET the tenant's warehouses (a small list)
//   distance    GET routing's distance between two points
// then waits a second, as a person would. Lists that grow with every run (shipments, arrivals) are
// not read: run after run, they would make the second side look slower.
//
// Two phases with the same users: a warm-up (WARMUP, 30 s), then the measured run (DURATION). The
// first requests after a quiet spell meet cold code (the JVM compiles as it goes, pools fill up);
// only the measured phase is counted, so neither side is judged on its first minute.
//
// Run with deploy/load/run.sh, which starts it as a Job in the cluster and samples the pods.
import http from 'k6/http';
import { check, sleep } from 'k6';

const EDGE = __ENV.EDGE_URL || 'http://edge:8081';
const IDENTITY = __ENV.IDENTITY_URL || 'http://identity:8085';
const NAMES = ['sign-in', 'plan', 'shipment', 'pickup', 'departure', 'deliver', 'warehouses', 'distance'];

const VUS = Number(__ENV.VUS || 10);
const WARMUP = __ENV.WARMUP || '30s';
const DURATION = __ENV.DURATION || '3m';

// Thresholds on the measured phase only (k6 tags every request with its scenario), always met:
// they are there so that the summary has figures for each request name.
const M = 'scenario:measured';
const thresholds = { [`http_req_failed{${M}}`]: ['rate<0.01'], [`http_reqs{${M}}`]: ['count>=0'],
  [`iterations{${M}}`]: ['count>=0'] };
for (const name of NAMES) {
  thresholds[`http_req_duration{${M},name:${name}}`] = ['max>=0'];
  thresholds[`http_reqs{${M},name:${name}}`] = ['count>=0'];
  thresholds[`http_req_failed{${M},name:${name}}`] = ['rate>=0'];
}

export const options = {
  scenarios: {
    warmup: { executor: 'constant-vus', vus: VUS, duration: WARMUP, gracefulStop: '5s' },
    measured: { executor: 'constant-vus', vus: VUS, duration: DURATION, startTime: WARMUP, gracefulStop: '30s' },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
  thresholds,
};

// A token lasts five minutes: each VU signs in at its start and again after four.
let token = null;
let signedInAt = 0;

function headers() {
  if (!token || Date.now() - signedInAt > 4 * 60 * 1000) {
    const res = http.post(`${IDENTITY}/sessions`,
      JSON.stringify({ username: __ENV.LOAD_USERNAME, password: __ENV.LOAD_PASSWORD }),
      { headers: { 'Content-Type': 'application/json' }, tags: { name: 'sign-in' } });
    check(res, { 'signed in': (r) => r.status === 200 });
    token = res.json('accessToken');
    signedInAt = Date.now();
  }
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

const SHIPMENT = JSON.stringify({
  consignorName: 'FF Hadimkoy', consigneeName: 'Istanbul Hadimkoy Hub', destinationGln: '8690001000011',
  equipmentCode: 'CSQU3054383', sscc: '106141411234567897', incoterm: 'DAP',
  origin: 'Hadimkoy Hub', destination: 'Esenyurt', grossWeightKg: 800,
  legs: [{ mode: 'ROAD', origin: 'Hadimkoy Hub', destination: 'Esenyurt',
    originLatitude: 41.10, originLongitude: 28.65, destinationLatitude: 41.03, destinationLongitude: 28.67,
    carrierReference: 'TR-34-FF-0417' }],
});

export default function () {
  const h = { headers: headers() };

  const planned = http.post(`${EDGE}/api/v1/tms/shipments`, SHIPMENT, { ...h, tags: { name: 'plan' } });
  if (!check(planned, { 'planned': (r) => r.status === 201 })) {
    sleep(1);
    return;
  }
  const id = planned.json('id');
  check(http.get(`${EDGE}/api/v1/tms/shipments/${id}`, { ...h, tags: { name: 'shipment' } }),
    { 'read': (r) => r.status === 200 });
  check(http.post(`${EDGE}/api/v1/tms/shipments/${id}/pickup`, null, { ...h, tags: { name: 'pickup' } }),
    { 'picked up': (r) => r.status === 200 });
  check(http.post(`${EDGE}/api/v1/tms/shipments/${id}/departure`, null, { ...h, tags: { name: 'departure' } }),
    { 'departed': (r) => r.status === 200 });
  check(http.post(`${EDGE}/api/v1/tms/shipments/${id}/delivery`, JSON.stringify({ receivedBy: 'Dock 2, k6' }),
    { ...h, tags: { name: 'deliver' } }), { 'delivered': (r) => r.status === 200 });
  check(http.get(`${EDGE}/api/v1/wms/warehouses`, { ...h, tags: { name: 'warehouses' } }),
    { 'warehouses': (r) => r.status === 200 });
  check(http.get(`${EDGE}/api/v1/geo/distance?fromLatitude=41.10&fromLongitude=28.65&toLatitude=41.03&toLongitude=28.67`,
    { ...h, tags: { name: 'distance' } }), { 'distance': (r) => r.status === 200 });
  sleep(1);
}

// One line per request name: count, failures, median, p95, p99, max. Written by hand rather than
// with k6's own summary, so the pod needs nothing from the internet.
export function handleSummary(data) {
  const m = data.metrics;
  const v = (key, stat) => (m[key] ? m[key].values[stat] : undefined);
  const ms = (x) => (x === undefined ? '-' : x.toFixed(1)).padStart(8);
  const seconds = (d) => Number(d.replace(/s$/, '').replace(/^(\d+)m$/, (_, n) => String(n * 60)));
  const measured = seconds(DURATION);
  const lines = [];
  lines.push(`side: ${__ENV.SIDE || '?'}  vus: ${VUS}  warm-up: ${WARMUP}  measured: ${DURATION}`);
  const iterations = v(`iterations{${M}}`, 'count') || 0;
  const requests = v(`http_reqs{${M}}`, 'count') || 0;
  lines.push(`iterations: ${iterations}  (${(iterations / measured).toFixed(2)}/s)  `
    + `requests: ${requests}  (${(requests / measured).toFixed(1)}/s)  `
    + `failed: ${((v(`http_req_failed{${M}}`, 'rate') || 0) * 100).toFixed(2)}%`);
  lines.push('');
  lines.push(`${'request'.padEnd(11)}${'count'.padStart(7)}${'failed'.padStart(8)}${'med ms'.padStart(8)}`
    + `${'p95 ms'.padStart(8)}${'p99 ms'.padStart(8)}${'max ms'.padStart(8)}`);
  for (const name of NAMES) {
    const d = `http_req_duration{${M},name:${name}}`;
    const count = v(`http_reqs{${M},name:${name}}`, 'count');
    if (!count) continue;
    lines.push(`${name.padEnd(11)}${String(count).padStart(7)}`
      + `${((v(`http_req_failed{${M},name:${name}}`, 'rate') || 0) * 100).toFixed(1).padStart(7)}%`
      + `${ms(v(d, 'med'))}${ms(v(d, 'p(95)'))}${ms(v(d, 'p(99)'))}${ms(v(d, 'max'))}`);
  }
  return { stdout: lines.join('\n') + '\n' };
}
