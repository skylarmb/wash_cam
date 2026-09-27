// Test inherited defaults against the actual values rendered from the database.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const {html, expected} = JSON.parse(fs.readFileSync(0, 'utf8'));
const script = html.split('<script>')[1].split('</script>')[0];
const remembered = JSON.stringify({bay_0: 2, bay_1: 0, bay_2: 1, bay_3: 0, bay_4: 3, vac_0: 1});
// Old array-shaped memories, partial objects, and invalid values are ignored.
for (const stored of [remembered, null, '{bad', '[2,0,1,0,3,1]', '{"bay_0":1}',
  '{"bay_0":true,"bay_1":0,"bay_2":0,"bay_3":0,"bay_4":0,"vac_0":0}', 'blocked']) {
  const inputs = Array.from(html.matchAll(/id="(\w+)" name="\w+" type="number" min="0" step="1" required value="(\d+)"/g), match => ({
    id: match[1], value: match[2], focus() {}, addEventListener() {}
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
  assert.deepEqual(inputs.map(input => Number(input.value)), stored === remembered ? expected : original);
}
