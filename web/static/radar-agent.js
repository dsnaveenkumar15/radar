"use strict";
(function () {
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };

  const form = document.getElementById("agent-form");
  if (form) {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const err = document.getElementById("agent-error");
      err.hidden = true;
      const pasteText = document.getElementById("paste-text").value.trim();
      const body = pasteText
        ? {filename: document.getElementById("paste-name").value.trim() || "requirements.txt", text: pasteText}
        : {repo_url: document.getElementById("repo-url").value.trim()};
      const r = await fetch("/api/agent/runs", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body)});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        err.textContent = typeof data.detail === "string" ? data.detail
          : (r.status === 413 ? "That file is too large; paste up to 600 KB." : "The run could not start. Check the URL or file and try again.");
        err.hidden = false;
        return;
      }
      location.href = "/agent/runs/" + data.id;
    });
  }

  const root = document.getElementById("agent-run");
  if (!root) return;
  const runId = root.dataset.run, started = performance.now();
  const board = document.getElementById("agent-board"), trace = document.getElementById("agent-trace");
  const results = document.getElementById("agent-results"), top = document.getElementById("agent-top");
  const cards = {};
  const setTop = (k, v) => { top.querySelector(`[data-k="${k}"]`).textContent = v; };
  const tick = setInterval(() => setTop("time", Math.round((performance.now() - started) / 1000) + " s"), 1000);

  function apply(e) {
    const p = e.payload || {};
    if (e.type === "node") {
      const n = document.querySelector(`.map-node[data-node="${p.name}"]`);
      if (n) {
        n.classList.toggle("is-active", p.status === "start");
        if (p.status === "end") n.classList.add("is-done");
      }
    } else if (e.type === "info") {
      trace.appendChild(el("li", "trace-info", p.text));
    } else if (e.type === "plan") {
      const li = el("li", "trace-plan", "Plan: ");
      p.order.forEach((o) => li.appendChild(el("span", "chip", o.id + " · " + o.reason)));
      trace.appendChild(li);
    } else if (e.type === "candidate") {
      let c = cards[p.id];
      if (!c) {
        c = cards[p.id] = el("div", "agent-card");
        c.appendChild(el("code", null, p.symbol || p.id));
        c.appendChild(el("span", "card-where", p.path ? p.path + ":" + p.line : ""));
        c.appendChild(el("span", "card-status"));
        board.appendChild(c);
      }
      c.dataset.status = p.status;
      c.querySelector(".card-status").textContent = p.status + (p.reason ? " · " + p.reason : "");
    } else if (e.type === "tool") {
      const li = el("li", "trace-tool"), d = el("details");
      d.appendChild(el("summary", null, `${p.candidate} → ${p.name}(${JSON.stringify(p.args)})`));
      d.appendChild(el("pre", null, p.result));
      li.appendChild(d);
      trace.appendChild(li);
    } else if (e.type === "verify") {
      trace.appendChild(el("li", p.ok ? "trace-ok" : "trace-bad", `${p.id}: ${p.ok ? "verified against your code" : "dropped: " + p.note}`));
    } else if (e.type === "usage") {
      setTop("model", p.model);
      setTop("tokens", p.total_tokens + " tokens");
      setTop("cost", "$" + p.total_usd.toFixed(4));
    } else if (e.type === "result") {
      renderResult(p);
    } else if (e.type === "error") {
      results.appendChild(el("p", "notice", p.text));
    }
  }

  function renderResult(p) {
    results.replaceChildren();
    results.appendChild(el("p", "result-summary", p.report.summary));
    (p.verified || []).forEach((f) => {
      const ev = (f.evidence || [])[0] || {};
      const box = el("div", "agent-finding");
      box.appendChild(el("p", null, `${ev.path}:${ev.line} · verified against your code`));
      box.appendChild(el("pre", "agent-diff", ev.quote || ""));
      box.appendChild(el("p", "muted", f.explanation || ""));
      results.appendChild(box);
    });
    (p.paths || []).forEach((path) => {
      const box = el("div", "agent-path");
      box.appendChild(el("h3", null, `${path.package}: ${path.used} → ${path.latest} · ${path.difficulty} (about ${path.effort_hours} h)`));
      if (path.safe_now) box.appendChild(el("p", null, `Safe now: up to ${path.safe_now}.`));
      path.steps.forEach((s) => box.appendChild(el("p", null, `${s.to}: ${s.changes} change(s) in ${s.files} file(s).`)));
      path.security.forEach((a) => box.appendChild(el("p", "notice", `${a.severity} advisory ${a.id}: ${a.summary}`)));
      results.appendChild(box);
    });
    (p.report.changes || []).forEach((c) => results.appendChild(el("pre", "agent-diff", c.diff)));
    if (p.report.patch) {
      const a = el("a", "btn", "Download patch");
      a.href = `/agent/runs/${runId}/patch.diff`;
      results.appendChild(a);
    }
    const b = el("a", "btn btn-quiet", "Download issue checklist");
    b.href = `/agent/runs/${runId}/issue.md`;
    results.appendChild(b);
    if (p.budget_note) results.appendChild(el("p", "muted", "Note: " + p.budget_note + "."));
    (p.notes || []).forEach((n) => results.appendChild(el("p", "muted", n)));
    if ((p.untracked || []).length) results.appendChild(el("p", "muted", "Not covered (Radar does not track): " + p.untracked.join(", ")));
    clearInterval(tick);
  }

  if (root.dataset.status === "running" || root.dataset.status === "queued") {
    const es = new EventSource(`/api/agent/runs/${runId}/events`);
    es.onmessage = (m) => apply(JSON.parse(m.data));
    es.addEventListener("end", () => { es.close(); clearInterval(tick); });
  } else {
    fetch(`/api/agent/runs/${runId}/all`).then((r) => r.json()).then(async (all) => {
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      for (const e of all) { apply(e); if (!reduce) await new Promise((res) => setTimeout(res, 120)); }
      clearInterval(tick);
    });
  }
})();
