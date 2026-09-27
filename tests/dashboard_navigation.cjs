// Execute the rendered page script with controlled DOM and network responses.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const direction = Number(process.argv[2]);
const outcome = process.argv[3];
const events = [];
const links = ['previous', 'next'].map(name => ({
  href: `/labeling?image_path=${name}.jpg`,
  addEventListener(type, callback) { this[type] = callback; }
}));
const elements = {
  labels: {
    reportValidity: () => outcome !== 'invalid',
    querySelectorAll: () => [2, 0, 1, 0, 3].map(value => ({value})),
    addEventListener(type, callback) { this[type] = callback; }
  },
  image_path: {value: 'capture.jpg'},
  save: {disabled: false},
  status: {textContent: ''}
};
const context = {
  document: {
    getElementById: id => elements[id],
    querySelectorAll: () => links
  },
  window: {location: {assign: url => events.push(['navigate', url])}},
  fetch: async (url, options) => {
    events.push(['save', url, JSON.parse(options.body)]);
    // Yield to ensure navigation waits for the save response.
    await Promise.resolve();
    return {ok: outcome === 'success', json: async () => ({error: 'Save failed.'})};
  }
};
vm.runInNewContext(fs.readFileSync(0, 'utf8'), context);
(async () => {
  assert.equal(typeof links[direction].click, 'function', 'Navigation must save before leaving');
  let prevented = false;
  await links[direction].click({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  if (outcome === 'invalid') {
    assert.deepEqual(events, []);
    return;
  }
  assert.deepEqual(events[0], ['save', '/api/labels', {
    image_path: 'capture.jpg', counts: [2, 0, 1, 0, 3]
  }]);
  if (outcome === 'success') {
    assert.deepEqual(events[1], ['navigate', links[direction].href]);
  } else {
    assert.equal(events.length, 1);
    assert.equal(elements.status.textContent, 'Save failed.');
  }
  assert.equal(elements.save.disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
