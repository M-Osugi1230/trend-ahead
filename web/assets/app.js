(() => {
  'use strict';

  const SAVED_KEY = 'trend-ahead:saved-ids:v1';
  const INTEREST_KEY = 'trend-ahead:interests:v1';
  const $ = id => document.getElementById(id);

  function loadSet(key) {
    try {
      const data = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(data) ? new Set(data.filter(x => typeof x === 'string').slice(0, 500)) : new Set();
    } catch (_) {
      return new Set();
    }
  }
  function persist(key, values) {
    try { localStorage.setItem(key, JSON.stringify([...values].slice(0, 500))); }
    catch (_) { /* Storage may be blocked; browser session still works. */ }
  }

  const saved = loadSet(SAVED_KEY);
  const interests = loadSet(INTEREST_KEY);
  const cards = [...document.querySelectorAll('.topic-card')];
  const topicList = $('topic-list');
  const search = $('topic-search');
  const category = $('category-filter');
  const savedOnly = $('saved-toggle');
  const personalizedToggle = $('personalized-toggle');
  const interestStatus = $('interest-status');
  const interestInputs = [...document.querySelectorAll('input[name="interest"]')];
  let showSaved = false;
  let personalized = false;

  function updateSavedButtons() {
    document.querySelectorAll('[data-save]').forEach(button => {
      const active = saved.has(button.dataset.save);
      button.textContent = active ? '★ 保存済み' : '☆ 保存';
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('is-saved', active);
    });
  }

  function orderCards() {
    if (!topicList) return;
    if (!personalized || !interests.size) {
      cards.forEach(node => topicList.appendChild(node));
      return;
    }
    const preferred = cards.filter(card => interests.has(card.dataset.category));
    const discovery = cards.filter(card => !interests.has(card.dataset.category));
    const arranged = [];
    // Keep one discovery slot for every three interest-matched topics.
    while (preferred.length || discovery.length) {
      for (let i = 0; i < 3 && preferred.length; i++) arranged.push(preferred.shift());
      if (discovery.length) arranged.push(discovery.shift());
    }
    arranged.forEach(node => topicList.appendChild(node));
  }

  function updateInterests() {
    interestInputs.forEach(input => { input.checked = interests.has(input.value); });
    if (personalizedToggle) {
      personalizedToggle.classList.toggle('active', personalized);
      personalizedToggle.setAttribute('aria-pressed', String(personalized));
      personalizedToggle.textContent = personalized ? 'おすすめ順：ON' : 'おすすめ順に表示';
    }
    if (interestStatus) {
      interestStatus.textContent = interests.size
        ? `${interests.size}ジャンルを選択中。おすすめ順でも他ジャンルの発見枠を残します。`
        : 'ジャンルを選ぶと、興味に合う話題を優先表示できます。';
    }
    document.querySelectorAll('.upcoming-card[data-category]').forEach(card => {
      card.classList.toggle('is-preferred', personalized && interests.has(card.dataset.category));
    });
    orderCards();
    filterCards();
  }

  function filterCards() {
    const q = (search?.value || '').trim().toLocaleLowerCase();
    const selected = category?.value || 'all';
    let count = 0;
    for (const item of cards) {
      const show = (!q || (item.dataset.search || '').includes(q)) &&
        (selected === 'all' || item.dataset.category === selected) &&
        (!showSaved || saved.has(item.dataset.id));
      item.hidden = !show;
      if (show) count++;
    }
    if ($('filter-empty')) $('filter-empty').hidden = !(cards.length && count === 0);
    if ($('result-count')) $('result-count').textContent = `${count}件の話題を表示`;
  }

  // Filter upcoming events in-place without a server round-trip.
  // Keep the opening screen scannable even as the verified catalog grows.
  // All cards remain in the static HTML and are visible without JavaScript.
  const futureCards = [...document.querySelectorAll('#future-list .upcoming-card')];
  const futureSearch = $('future-search');
  const futureCategory = $('future-category');
  const futureRangeButtons = [...document.querySelectorAll('[data-future-window]')];
  const futureMore = $('future-load-more');
  const PAGE_SIZE = 12;
  let futureWindow = 'all';
  let futureLimit = PAGE_SIZE;

  function filterFuture() {
    const query = (futureSearch?.value || '').trim().toLocaleLowerCase();
    const selected = futureCategory?.value || 'all';
    const now = Date.now();
    const horizon = futureWindow === 'all' ? Infinity : Number(futureWindow) * 86400000;
    let matched = 0;
    let shown = 0;
    for (const card of futureCards) {
      const start = Date.parse(card.dataset.startsAt || '');
      const matchesDate = futureWindow === 'all' ||
        (Number.isFinite(start) && start >= now && start - now <= horizon);
      const matchesCategory = selected === 'all' || card.dataset.category === selected;
      const matchesSearch = !query || (card.dataset.search || '').includes(query);
      const matches = matchesDate && matchesCategory && matchesSearch;
      if (matches) matched++;
      const show = matches && matched <= futureLimit;
      card.hidden = !show;
      if (show) shown++;
    }
    const status = $('future-result-status');
    if (status) status.textContent = matched === shown
      ? String(matched) + '件'
      : String(matched) + '件中 ' + String(shown) + '件を表示';
    const empty = $('future-empty');
    if (empty) empty.hidden = !(futureCards.length && matched === 0);
    if (futureMore) {
      futureMore.hidden = shown >= matched;
      futureMore.textContent = 'さらに表示（残り' + (matched - shown) + '件） ＋';
    }
  }

  futureMore?.addEventListener('click', () => {
    futureLimit += PAGE_SIZE;
    filterFuture();
  });
  futureRangeButtons.forEach(button => button.addEventListener('click', () => {
    futureWindow = button.dataset.futureWindow || 'all';
    futureLimit = PAGE_SIZE;
    for (const option of futureRangeButtons) {
      const active = option === button;
      option.classList.toggle('is-active', active);
      option.setAttribute('aria-pressed', String(active));
    }
    filterFuture();
  }));
  futureSearch?.addEventListener('input', () => {
    futureLimit = PAGE_SIZE;
    filterFuture();
  });
  futureCategory?.addEventListener('change', () => {
    futureLimit = PAGE_SIZE;
    filterFuture();
  });

  document.addEventListener('click', async ev => {
    const button = ev.target.closest('[data-save]');
    if (button) {
      const id = button.dataset.save;
      if (saved.has(id)) saved.delete(id); else saved.add(id);
      persist(SAVED_KEY, saved);
      updateSavedButtons();
      filterCards();
    }
    const share = ev.target.closest('[data-share]');
    if (share) {
      const link = new URL(share.dataset.share, location.href).href;
      const title = share.dataset.title || 'Trend Ahead';
      if (navigator.share) {
        try { await navigator.share({ title, url: link }); } catch (_) { /* User cancelled. */ }
      } else if (navigator.clipboard?.writeText) {
        try { await navigator.clipboard.writeText(link); share.textContent = 'URLをコピーしました ✓'; }
        catch (_) { share.textContent = 'URL: ' + link; }
      } else { share.textContent = 'URL: ' + link; }
    }
  });

  interestInputs.forEach(input => input.addEventListener('change', () => {
    if (input.checked) interests.add(input.value); else interests.delete(input.value);
    persist(INTEREST_KEY, interests);
    if (interests.size) personalized = true;
    updateInterests();
  }));
  personalizedToggle?.addEventListener('click', () => {
    personalized = !personalized;
    updateInterests();
  });
  search?.addEventListener('input', filterCards);
  category?.addEventListener('change', filterCards);
  savedOnly?.addEventListener('click', () => {
    showSaved = !showSaved;
    savedOnly.setAttribute('aria-pressed', String(showSaved));
    savedOnly.classList.toggle('active', showSaved);
    filterCards();
  });

  function handleHash() {
    if (location.hash === '#saved' && savedOnly) {
      showSaved = true;
      savedOnly.setAttribute('aria-pressed', 'true');
      savedOnly.classList.add('active');
      $('now')?.scrollIntoView({ behavior: 'smooth' });
    }
    filterCards();
  }
  window.addEventListener('hashchange', handleHash);
  updateSavedButtons();
  updateInterests();
  filterFuture();
  handleHash();
})();

