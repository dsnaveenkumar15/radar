"use strict";
(function () {
  function matchesStack(affects, stack) {
    if (!stack.length || !affects.length) return true;   // no stack chosen, or a cluster we could not tag: show it
    return affects.some(function (tag) { return stack.indexOf(tag) !== -1; });
  }

  function isNew(generated, previous) {
    return previous > 0 && generated > previous;
  }

  const api = {matchesStack: matchesStack, isNew: isNew};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;
  window.atlasRadar = api;

  function storage(kind) {
    try { return window[kind]; } catch (e) { return null; }   // blocked storage: everything below still works
  }
  function read(store, key) {
    if (!store) return null;
    try { return store.getItem(key); } catch (e) { return null; }
  }
  function write(store, key, value) {
    if (!store) return;
    try { store.setItem(key, value); } catch (e) { /* ignore */ }
  }
  function words(text) {
    return (text || "").split(/\s+/).filter(Boolean);
  }

  const local = storage("localStorage");
  const session = storage("sessionStorage");
  const articles = Array.prototype.slice.call(document.querySelectorAll("article.cluster"));

  // Since last visit. The previous value is kept for the tab's session so a reload does not lose the highlights.
  let previous;
  const kept = read(session, "radar:previous");
  if (kept === null) {
    previous = Number(read(local, "radar:last")) || 0;
    write(session, "radar:previous", String(previous));
  } else {
    previous = Number(kept) || 0;
  }
  write(local, "radar:last", String(Math.floor(Date.now() / 1000)));
  articles.forEach(function (article) {
    if (isNew(Number(article.dataset.generated) || 0, previous)) {
      article.classList.add("is-new");
      const badge = article.querySelector(".badge.new");
      if (badge) badge.hidden = false;
    }
  });

  // My stack.
  const panel = document.getElementById("stack");
  const options = document.getElementById("stack-options");
  const count = document.getElementById("stack-count");
  if (!panel || !options || !count) return;
  const tags = words(panel.dataset.tags);
  if (!tags.length) return;
  let stack = [];
  try {
    stack = JSON.parse(read(local, "radar:stack") || "[]").filter(function (tag) { return tags.indexOf(tag) !== -1; });
  } catch (e) {
    stack = [];
  }

  function apply() {
    let shown = 0;
    articles.forEach(function (article) {
      const show = matchesStack(words(article.dataset.affects), stack);
      article.hidden = !show;
      if (show) shown += 1;
    });
    count.textContent = stack.length ? shown + " of " + articles.length + " clusters match your stack" : "";
  }

  tags.forEach(function (tag) {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = stack.indexOf(tag) !== -1;
    box.addEventListener("change", function () {
      stack = tags.filter(function (t) { return t === tag ? box.checked : stack.indexOf(t) !== -1; });
      write(local, "radar:stack", JSON.stringify(stack));
      apply();
    });
    label.append(box, document.createTextNode(" " + tag));
    options.append(label);
  });
  panel.hidden = false;
  apply();
})();
