/* A dependency-free integration test for date/category/text filtering in app.js. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const now = Date.now();
function makeCard(days, category, search) {
  return {
    dataset: {
      startsAt: new Date(now + days * 86400000).toISOString(),
      category,
      search
    },
    hidden: false,
    classList: { toggle() {} }
  };
}

function element(value = '') {
  const listeners = Object.create(null);
  return {
    value,
    textContent: '',
    hidden: true,
    dataset: {},
    listeners,
    classList: { toggle() {}, add() {} },
    addEventListener(name, fn) { listeners[name] = fn; },
    setAttribute() {}
  };
}

const cards = [
  makeCard(2, 'ゲーム', 'ゲーム発表会'),
  makeCard(14, 'スポーツ', 'サッカー決勝'),
  makeCard(45, '映画・アニメ', 'アニメイベント')
];
const search = element();
const category = element('all');
const count = element();
const empty = element();
const more = element();
const buttons = Object.fromEntries(['all', '7', '30'].map(key => {
  const button = element();
  button.dataset.futureWindow = key;
  return [key, button];
}));
const ids = {
  'future-search': search,
  'future-category': category,
  'future-result-status': count,
  'future-empty': empty,
  'future-load-more': more
};
const document = {
  getElementById(id) { return ids[id] || null; },
  addEventListener() {},
  querySelectorAll(selector) {
    if (selector === '#future-list .upcoming-card') return cards;
    if (selector === '.upcoming-card[data-category]') return cards;
    if (selector === '[data-future-window]') return Object.values(buttons);
    return [];
  }
};
const app = fs.readFileSync(path.resolve(__dirname, '../web/assets/app.js'), 'utf8');
vm.runInNewContext(app, {
  document,
  location: { hash: '' },
  localStorage: { getItem() { return '[]'; }, setItem() {} },
  window: { addEventListener() {} },
  navigator: {},
  Date,
  Set,
  URL,
  console
}, { timeout: 1500 });

assert.deepEqual(cards.map(c => c.hidden), [false, false, false]);
assert.match(count.textContent, /^3件/);
buttons['7'].listeners.click();
assert.deepEqual(cards.map(c => c.hidden), [false, true, true]);
assert.match(count.textContent, /^1件/);
buttons['30'].listeners.click();
assert.deepEqual(cards.map(c => c.hidden), [false, false, true]);
category.value = 'スポーツ';
category.listeners.change();
assert.deepEqual(cards.map(c => c.hidden), [true, false, true]);
search.value = '存在しない';
search.listeners.input();
assert.deepEqual(cards.map(c => c.hidden), [true, true, true]);
assert.equal(empty.hidden, false);
search.value = 'サッカー';
search.listeners.input();
assert.deepEqual(cards.map(c => c.hidden), [true, false, true]);
assert.equal(empty.hidden, true);
buttons.all.listeners.click();
category.value = 'all';
category.listeners.change();
search.value = '';
search.listeners.input();
assert.deepEqual(cards.map(c => c.hidden), [false, false, false]);
// Once the catalog expands, only 12 items should appear until explicitly requested.
for (let i = 0; i < 25; i++) cards.push(makeCard(i + 1, 'ゲーム', '追加イベント'));
search.value = '';
category.value = 'all';
buttons.all.listeners.click();
assert.equal(cards.filter(card => !card.hidden).length, 12);
assert.equal(more.hidden, false);
assert.match(count.textContent, /^28件中 12件を表示/);
more.listeners.click();
assert.equal(cards.filter(card => !card.hidden).length, 24);
assert.equal(more.hidden, false);
more.listeners.click();
assert.equal(cards.filter(card => !card.hidden).length, 28);
assert.equal(more.hidden, true);
search.value = '存在しない';
search.listeners.input();
assert.equal(cards.filter(card => !card.hidden).length, 0);
assert.equal(more.hidden, true);
search.value = '追加イベント';
search.listeners.input();
assert.equal(cards.filter(card => !card.hidden).length, 12);
assert.match(count.textContent, /^25件中 12件を表示/);

console.log('Future filter browser-script tests passed');
