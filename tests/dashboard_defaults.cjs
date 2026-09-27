// Test inherited defaults against the actual values rendered from the database.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {html, expected} = JSON.parse(fs.readFileSync(0, 'utf8'));
const script = html.split('<script>')[1].split('</script>')[0];
for (const stored of [JSON.stringify([2, 0, 1, 0, 3]), null, '{bad', '[1]', '[true,0,0,0,0]', 'blocked']) {
  const inputs = Array.from(html.matchAll(/required value="(\d+)"/g), match => ({
    value: match[1], focus() {}, addEventListener() {}
  }));
  const original = inputs.map(input => Number(input.value));
  vm.runInNewContext(script, {
    document: {
      getElementById: id => id === 'labels' ? {
        querySelectorAll: () => inputs, addEventListener() {}
      } : {value: 'capture.jpg'},
      querySelector: () => ({href: '/labeling?image_path=next.jpg'}),
      querySelectorAll: () => []
    },
    sessionStorage: {
      getItem() { if (stored === 'blocked') throw new Error('Unavailable'); return stored; }
    }
  });
  assert.deepEqual(inputs.map(input => Number(input.value)), stored === JSON.stringify([2, 0, 1, 0, 3]) ? expected : original);
}
