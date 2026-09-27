// Execute the rendered page script with controlled DOM and network responses.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const direction = process.argv[2];
const keyboard = direction === "keyboard" || direction === "last_space";
let focused;
const outcome = process.argv[3];
const events = [];
let remembered = null;
const links = ['previous', 'next'].map(name => ({
  href: `/labeling?image_path=${name}.jpg`,
  addEventListener(type, callback) { this[type] = callback; }
}));
const bayIds = ['bay_0', 'bay_1', 'bay_2', 'bay_3', 'bay_4', 'vac_0'];
// Key a list of counts by bay ID, in page order.
const keyed = counts => Object.fromEntries(bayIds.map((id, index) => [id, counts[index]]));
const inputs = (keyboard ? [0, 0, 0, 0, 0, 0] : [2, 0, 1, 0, 3, 1]).map((value, index) => ({
  id: bayIds[index],
  value,
  focus() { focused = this; },
  stepUp() { this.value = Number(this.value) + 1; },
  addEventListener(type, callback) { this[type] = callback; }
}));
const elements = {
  labels: {
    reportValidity: () => outcome !== 'invalid',
    querySelectorAll: () => inputs,
    addEventListener(type, callback) { this[type] = callback; }
  },
  image_path: {value: 'capture.jpg'},
  save: {disabled: false, focus() { focused = this; }},
  status: {textContent: ''}
};
const context = {
  sessionStorage: {
    getItem: () => remembered,
    setItem(key, value) { remembered = value; }
  },
  document: {
    getElementById: id => elements[id],
    querySelectorAll: () => links,
    querySelector: () => links[1]
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
  let prevented = false;
  if (keyboard) {
    assert.equal(focused, inputs[0], 'Focus Bay 0 on page load');
    // Tab keeps native focus behavior. Space increments and advances focus.
    const space = {key: ' ', preventDefault() {}};
    if (direction === 'keyboard') {
      inputs[2].focus();
      await inputs[2].keydown(space);
      assert.equal(focused, inputs[3]);
      await inputs[3].keydown(space);
      assert.equal(focused, inputs[4]);
    } else {
      inputs[5].focus();
      await inputs[5].keydown(space);
      assert.equal(focused, elements.save);
    }
    await elements.labels.submit({preventDefault() { prevented = true; }});
  } else {
    assert.equal(typeof links[direction].click, 'function', 'Navigation must save before leaving');
    await links[direction].click({preventDefault() { prevented = true; }});
  }
  assert.equal(prevented, true);
  if (outcome === 'invalid') {
    assert.deepEqual(events, []);
    return;
  }
  assert.deepEqual(events[0], ['save', '/api/labels', {
    image_path: 'capture.jpg', counts: keyed(direction === 'keyboard' ? [0, 0, 1, 1, 0, 0] : direction === 'last_space' ? [0, 0, 0, 0, 0, 1] : [2, 0, 1, 0, 3, 1])
  }]);
  if (outcome === 'success') {
    assert.deepEqual(events[1], ['navigate', links[keyboard ? 1 : direction].href]);
    assert.deepEqual(JSON.parse(remembered), events[0][2].counts);
  } else {
    assert.equal(events.length, 1);
    assert.equal(remembered, null);
    assert.equal(elements.status.textContent, 'Save failed.');
  }
  assert.equal(elements.save.disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
