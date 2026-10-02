"use strict";
(function () {
  function safeHref(url) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {safeHref: safeHref};
  if (typeof document === "undefined") return;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function render(out, data) {
    out.replaceChildren();
    out.append(el("p", data.kind, "badge"));
    for (const sentence of data.sentences) {
      const p = el("p", sentence.text);
      for (const cite of sentence.cites) {
        const href = cite.url ? safeHref(cite.url) : null;
        p.append(document.createTextNode(" "));
        if (href) {
          const a = el("a", "[" + cite.title + "]");
          a.href = href;
          a.rel = "nofollow noopener";
          p.append(a);
        } else {
          p.append(el("span", "[" + cite.title + "]", "muted"));
        }
      }
      out.append(p);
    }
    if (data.note) out.append(el("p", data.note, "muted"));
  }

  const form = document.getElementById("ask-form");
  const out = document.getElementById("ask-result");
  if (!form || !out) return;
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    out.replaceChildren(el("p", "Searching…", "muted"));
    let response;
    try {
      response = await fetch("/api/ask", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({question: form.question.value}),
      });
    } catch (e) {
      out.replaceChildren(el("p", "Could not reach the server."));
      return;
    }
    const data = await response.json().catch(function () { return {}; });
    if (response.status === 429) {
      out.replaceChildren(el("p", "Too many questions. Try again in a minute."));
    } else if (response.status === 503) {
      out.replaceChildren(el("p", "The answering model is busy. Try again in a few seconds."));
    } else if (!response.ok) {
      out.replaceChildren(el("p", typeof data.detail === "string" ? data.detail : "Asking failed (" + response.status + ")"));
    } else {
      render(out, data);
    }
  });
})();
