"use strict";
(function () {
  const MAX_CHARS = 6000000;
  const MAX_DEPS = 5000;

  // ---------- version ordering (mirrors radar/versions.py: PEP 440 for pypi, semver for npm) ----------
  const PEP = /^v?(?:(\d+)!)?(\d+(?:\.\d+)*)(?:[-._]?(a|b|c|rc|alpha|beta|pre|preview)[-._]?(\d*))?(?:-(\d+)|[-._]?(post|rev|r)[-._]?(\d*))?(?:[-._]?dev[-._]?(\d*))?(?:\+([a-z0-9]+(?:[-_.][a-z0-9]+)*))?$/i;
  const SEMVER = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;
  const PRE_RANK = {a: 1, alpha: 1, b: 2, beta: 2, c: 3, rc: 3, pre: 3, preview: 3};

  function pepKey(version) {
    const m = PEP.exec(String(version).trim());
    if (!m) return null;
    const nums = m[2].split(".").map(Number);
    while (nums.length > 1 && nums[nums.length - 1] === 0) nums.pop();
    const hasPre = m[3] !== undefined, hasPost = m[5] !== undefined || m[6] !== undefined, hasDev = m[8] !== undefined;
    let pre;
    if (hasPre) pre = [PRE_RANK[m[3].toLowerCase()], Number(m[4] || 0)];
    else if (!hasPost && hasDev) pre = [-1, 0];
    else pre = [9, 0];
    const post = hasPost ? [1, Number(m[5] !== undefined ? m[5] : (m[7] || 0))] : [0, 0];
    const dev = hasDev ? [0, Number(m[8] || 0)] : [1, 0];
    const local = m[9] === undefined ? null : m[9].toLowerCase().split(/[-_.]/).map(function (s) { return /^\d+$/.test(s) ? [1, Number(s)] : [0, s]; });
    return {epoch: Number(m[1] || 0), nums: nums, rest: [pre, post, dev], local: local};
  }

  function cmpLocal(a, b) {
    if (a === null && b === null) return 0;
    if (a === null) return -1;
    if (b === null) return 1;
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if (a[i] === undefined) return -1;
      if (b[i] === undefined) return 1;
      if (a[i][0] !== b[i][0]) return a[i][0] < b[i][0] ? -1 : 1;
      if (a[i][1] !== b[i][1]) return a[i][1] < b[i][1] ? -1 : 1;
    }
    return 0;
  }

  function cmpArrays(a, b) {
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i++) {
      const x = a[i] === undefined ? 0 : a[i], y = b[i] === undefined ? 0 : b[i];
      if (x !== y) return x < y ? -1 : 1;
    }
    return 0;
  }

  function cmpPep(a, b) {
    const x = pepKey(a), y = pepKey(b);
    if (!x || !y) return String(a) === String(b) ? 0 : (String(a) < String(b) ? -1 : 1);
    if (x.epoch !== y.epoch) return x.epoch < y.epoch ? -1 : 1;
    const c = cmpArrays(x.nums, y.nums);
    if (c) return c;
    for (let i = 0; i < 3; i++) {
      const d = cmpArrays(x.rest[i], y.rest[i]);
      if (d) return d;
    }
    return cmpLocal(x.local, y.local);
  }

  function cmpSemver(a, b) {
    const x = SEMVER.exec(String(a).trim()), y = SEMVER.exec(String(b).trim());
    if (!x || !y) return String(a) === String(b) ? 0 : (String(a) < String(b) ? -1 : 1);
    const c = cmpArrays([+x[1], +x[2], +x[3]], [+y[1], +y[2], +y[3]]);
    if (c) return c;
    if (x[4] === undefined && y[4] === undefined) return 0;
    if (x[4] === undefined) return 1;
    if (y[4] === undefined) return -1;
    const p = x[4].split("."), q = y[4].split(".");
    for (let i = 0; i < Math.max(p.length, q.length); i++) {
      if (p[i] === undefined) return -1;
      if (q[i] === undefined) return 1;
      const pn = /^\d+$/.test(p[i]), qn = /^\d+$/.test(q[i]);
      if (pn && qn) { if (+p[i] !== +q[i]) return +p[i] < +q[i] ? -1 : 1; }
      else if (pn) return -1;
      else if (qn) return 1;
      else if (p[i] !== q[i]) return p[i] < q[i] ? -1 : 1;
    }
    return 0;
  }

  function cmp(ecosystem, a, b) { return ecosystem === "npm" ? cmpSemver(a, b) : cmpPep(a, b); }

  // ---------- dependency file parsing ----------
  function normPy(name) { return name.toLowerCase().replace(/[-_.]+/g, "-"); }

  function parseRequirements(text) {
    const deps = [];
    let skipped = 0;
    const joined = text.replace(/\\\r?\n/g, " ");
    for (const raw of joined.split(/\r?\n/)) {
      const line = raw.replace(/\s#.*$/, "").replace(/^#.*$/, "").trim();
      if (!line) continue;
      if (/^(-|https?:|git\+|file:|\.|\/)/i.test(line)) { skipped++; continue; }
      const m = /^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;]*)/.exec(line);
      if (!m) { skipped++; continue; }
      const specs = [];
      for (const part of m[2].split(",")) {
        const s = /^\s*(===|==|~=|>=|<=|!=|<|>)\s*([0-9][A-Za-z0-9.+!_*-]*)/.exec(part);
        if (s) specs.push({op: s[1], version: s[2]});
      }
      const eq = specs.find(function (s) { return s.op === "==" || s.op === "==="; });
      const min = specs.find(function (s) { return s.op === ">=" || s.op === "~="; });
      const pick = eq || min;
      if (!pick) { skipped++; continue; }
      const wildcard = /\.\*$/.test(pick.version);
      deps.push({ecosystem: "pypi", name: normPy(m[1]), version: pick.version.replace(/\.\*$/, ""), exact: Boolean(eq) && !wildcard});
    }
    return {deps: deps, skipped: skipped};
  }

  function rangeVersion(spec) {
    if (typeof spec !== "string") return null;
    const m = /^\s*(\^|~|>=|=|v)?\s*(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s*(?:(?:<|\|\|).*)?$/.exec(spec);
    return m ? {version: m[2], exact: !m[1] || m[1] === "=" || m[1] === "v"} : null;
  }

  function parsePackageJson(data) {
    const deps = [];
    let skipped = 0;
    for (const section of ["dependencies", "devDependencies", "optionalDependencies"]) {
      const block = data[section];
      if (!block || typeof block !== "object" || Array.isArray(block)) continue;
      for (const name of Object.keys(block)) {
        const r = rangeVersion(block[name]);
        if (r) deps.push({ecosystem: "npm", name: name, version: r.version, exact: r.exact}); else skipped++;
      }
    }
    return {deps: deps, skipped: skipped};
  }

  function parsePackageLock(data) {
    const deps = [];
    const packages = data.packages;
    if (packages && typeof packages === "object" && !Array.isArray(packages)) {
      for (const key of Object.keys(packages)) {
        const i = key.lastIndexOf("node_modules/");
        const entry = packages[key];
        if (i < 0 || !entry || typeof entry.version !== "string") continue;
        deps.push({ecosystem: "npm", name: key.slice(i + "node_modules/".length), version: entry.version, exact: true});
      }
    } else if (data.dependencies && typeof data.dependencies === "object") {
      for (const name of Object.keys(data.dependencies)) {
        const entry = data.dependencies[name];
        if (entry && typeof entry.version === "string") deps.push({ecosystem: "npm", name: name, version: entry.version, exact: true});
      }
    }
    return {deps: deps, skipped: 0};
  }

  function parseTomlLock(text) {
    const deps = [];
    for (const block of text.split(/^\[\[package\]\]\s*$/m).slice(1)) {
      const name = /^name\s*=\s*"([^"]+)"/m.exec(block), version = /^version\s*=\s*"([^"]+)"/m.exec(block);
      if (name && version) deps.push({ecosystem: "pypi", name: normPy(name[1]), version: version[1], exact: true});
    }
    return {deps: deps, skipped: 0};
  }

  function parsePipfileLock(data) {
    const deps = [];
    let skipped = 0;
    for (const section of ["default", "develop"]) {
      const block = data[section];
      if (!block || typeof block !== "object" || Array.isArray(block)) continue;
      for (const name of Object.keys(block)) {
        const entry = block[name];
        const v = entry && typeof entry.version === "string" ? /^==\s*([0-9][A-Za-z0-9.+!_-]*)$/.exec(entry.version) : null;
        if (v) deps.push({ecosystem: "pypi", name: normPy(name), version: v[1], exact: true}); else skipped++;
      }
    }
    return {deps: deps, skipped: skipped};
  }

  function parseYarnLock(text) {
    const deps = [];
    const seen = new Set();
    for (const block of text.split(/\n\s*\n/)) {
      const lines = block.split("\n").filter(function (l) { return l && l.charAt(0) !== "#"; });
      if (!lines.length || !/:\s*$/.test(lines[0])) continue;
      const version = /^\s+version\s+"([^"]+)"/m.exec(block);
      const first = lines[0].replace(/:\s*$/, "").split(",")[0].trim().replace(/^"|"$/g, "");
      const at = first.lastIndexOf("@");
      if (!version || at <= 0) continue;
      const key = first.slice(0, at) + "@" + version[1];
      if (seen.has(key)) continue;
      seen.add(key);
      deps.push({ecosystem: "npm", name: first.slice(0, at), version: version[1], exact: true});
    }
    return {deps: deps, skipped: 0};
  }

  function parseDeps(input) {
    const truncated = typeof input === "string" && input.length > MAX_CHARS;
    const text = typeof input === "string" ? input.slice(0, MAX_CHARS) : "";
    const trimmed = text.trim();
    let result = {deps: [], skipped: 0};
    try {
      if (trimmed.charAt(0) === "{") {
        const data = JSON.parse(trimmed);
        if (data && typeof data === "object" && !Array.isArray(data)) {
          result = "_meta" in data && ("default" in data || "develop" in data) ? parsePipfileLock(data) : ("lockfileVersion" in data || ("packages" in data && !("dependencies" in data && !("lockfileVersion" in data)))) && !("devDependencies" in data)
            ? parsePackageLock(data) : parsePackageJson(data);
        }
      } else if (/^lockfileVersion:/m.test(text)) {
        result = {deps: [], skipped: 0, unsupported: "pnpm-lock.yaml"};
      } else if (/^# yarn lockfile v1/m.test(text)) {
        result = parseYarnLock(text);
      } else if (/^\[\[package\]\]/m.test(text)) {
        result = parseTomlLock(text);
      } else if (trimmed) {
        result = parseRequirements(text);
      }
    } catch (e) {
      result = {deps: [], skipped: 0};
    }
    result.deps = result.deps.slice(0, MAX_DEPS);
    result.truncated = truncated;
    return result;
  }

  // ---------- the report ----------
  function list(value) { return Array.isArray(value) ? value : []; }

  function buildReport(data, deps) {
    const packages = data && typeof data.packages === "object" && data.packages && !Array.isArray(data.packages) ? data.packages : {};
    return deps.map(function (dep) {
      const eco = dep.ecosystem;
      const info = packages[eco + ":" + dep.name];
      const row = {name: dep.name, ecosystem: eco, pinned: dep.version, exact: dep.exact, tracked: Boolean(info), latest: null,
                   status: "untracked", notes: [], breaking: [], deprecations: [], advisories: [], migrations: []};
      if (!info || typeof info !== "object") { row.tracked = false; return row; }
      const versions = list(info.versions);
      const latest = typeof info.latest_stable === "string" ? info.latest_stable : null;
      row.latest = latest;
      if (!dep.version) { row.status = "unpinned"; return row; }
      if (!versions.length || !latest) {
        row.status = "nodata";
        row.notes.push("Radar has no API data for this package yet, so nothing is known about " + dep.version + ".");
        return row;
      }
      if (cmp(eco, dep.version, latest) > 0) {
        row.status = "newer";
        row.notes.push("Newer than any version Radar has checked (latest known " + latest + "); nothing is known about it.");
        return row;
      }
      if (!dep.exact) row.notes.push("Only a minimum version is given; the report assumes " + dep.version + ".");
      if (versions.length && cmp(eco, dep.version, versions[0]) < 0) {
        row.notes.push("Radar's API data starts at " + versions[0] + "; changes between " + dep.version + " and " + versions[0] + " are not covered.");
      }
      row.status = latest && cmp(eco, dep.version, latest) < 0 ? "behind" : "current";
      if (versions.length && versions.indexOf(dep.version) < 0 && cmp(eco, dep.version, versions[0]) > 0 && cmp(eco, dep.version, latest) < 0) {
        row.notes.push("Your version sits between two versions Radar has API data for, so the report may include changes made before it.");
      }
      const inWindow = function (to) { return cmp(eco, to, dep.version) > 0 && (!latest || cmp(eco, to, latest) <= 0); };
      for (const d of list(info.diffs)) {
        if (!d || !inWindow(d.to)) continue;
        for (const c of list(d.breaking)) row.breaking.push({from: d.from, to: d.to, symbol: c.symbol, detail: c.detail});
        for (const c of list(d.deprecations)) row.deprecations.push({from: d.from, to: d.to, symbol: c.symbol, detail: c.detail});
        if (d.truncated) row.notes.push("Some changes between " + d.from + " and " + d.to + " are not listed.");
      }
      const known = versions.indexOf(dep.version) >= 0;
      if (typeof info.advisories_omitted === "number" && info.advisories_omitted > 0) {
        row.notes.push(info.advisories_omitted + " older advisories are not listed; check osv.dev for " + dep.name + ".");
      }
      for (const a of list(info.advisories)) {
        if (!a) continue;
        const fixed = list(a.fixed_in);
        let state = "unknown";
        if (known) {
          if (list(a.affected_versions).indexOf(dep.version) >= 0) state = "affected";
          else if (list(a.undetermined_versions).indexOf(dep.version) >= 0) state = "unknown";
          else state = "not affected";
        } else if (a.closed === true && fixed.length && fixed.every(function (f) { return cmp(eco, f, dep.version) <= 0; })) {
          state = "not affected";
        }
        if (state !== "not affected") row.advisories.push({id: a.id, severity: a.severity, summary: a.summary, state: state, fixed_in: fixed});
      }
      for (const m of list(info.migrations)) if (m && inWindow(m.to)) row.migrations.push(m);
      return row;
    });
  }

  if (typeof module !== "undefined" && module.exports) module.exports = {parseDeps: parseDeps, cmp: cmp, buildReport: buildReport};
  if (typeof document === "undefined") return;

  // ---------- page ----------
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function renderRow(out, row) {
    const box = el("section", undefined, "finding");
    box.append(el("h3", row.name + " " + (row.pinned || "(no version)") + (row.latest ? " → " + row.latest : "")));
    if (row.status === "untracked") { box.append(el("p", "Radar does not track this package, so nothing is known about it. That is not the same as safe.", "muted")); out.append(box); return; }
    if (row.status === "nodata" || row.status === "newer") {
      for (const note of row.notes) box.append(el("p", note, "muted"));
      out.append(box);
      return;
    }
    if (row.status === "unpinned") { box.append(el("p", "No version is pinned, so Radar cannot say what changes for you.", "muted")); out.append(box); return; }
    box.append(el("p", row.status === "current" ? "At the latest version Radar knows." : row.breaking.length + " breaking change(s), " + row.deprecations.length + " deprecation(s), " + row.advisories.length + " advisory(ies).", "badge"));
    for (const note of row.notes) box.append(el("p", note, "muted"));
    const section = function (title, items, line) {
      if (!items.length) return;
      box.append(el("h4", title));
      const ul = el("ul");
      for (const it of items) ul.append(el("li", line(it), "mono"));
      box.append(ul);
    };
    section("Breaking changes", row.breaking, function (c) { return c.from + " → " + c.to + " · " + c.symbol + " · " + c.detail; });
    section("Deprecations", row.deprecations, function (c) { return c.from + " → " + c.to + " · " + c.symbol; });
    section("Advisories", row.advisories, function (a) { return a.id + " (" + a.severity + ") " + a.state + (a.state === "unknown" ? " — check manually" : "") + (a.fixed_in.length ? " · fixed in " + a.fixed_in.join(", ") : "") + " · " + a.summary; });
    if (row.migrations.length) {
      box.append(el("h4", "Migrations"));
      for (const m of row.migrations) {
        box.append(el("p", m.title + " (" + m.from + " → " + m.to + ", " + m.language + ")", "muted"));
        box.append(el("pre", "before:\n" + m.before + "\n\nafter:\n" + m.after));
      }
    }
    out.append(box);
  }

  const form = document.getElementById("upgrade-form");
  const area = document.getElementById("deps");
  const out = document.getElementById("upgrade-result");
  const file = document.getElementById("deps-file");
  let tooBig = false;
  if (!form || !area || !out) return;

  if (file) file.addEventListener("change", function () {
    const chosen = file.files && file.files[0];
    if (!chosen) return;
    const reader = new FileReader();
    tooBig = chosen.size > MAX_CHARS;
    reader.onload = function () { area.value = String(reader.result || "").slice(0, MAX_CHARS); };
    reader.readAsText(chosen.slice(0, MAX_CHARS));
  });

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    out.replaceChildren(el("p", "Reading…", "muted"));
    const parsed = parseDeps(area.value);
    if (parsed.unsupported) { out.replaceChildren(el("p", parsed.unsupported + " is not supported yet. Paste package.json or package-lock.json instead.")); return; }
    const cut = parsed.truncated || tooBig ? "The input is longer than 6 MB; only the start was read. " : "";
    if (!parsed.deps.length) { out.replaceChildren(el("p", cut + "No pinned dependencies found in that text.")); return; }
    let data;
    try {
      const response = await fetch("/upgrade.json");
      data = await response.json();
    } catch (e) {
      out.replaceChildren(el("p", "Could not load Radar's data."));
      return;
    }
    const rows = buildReport(data, parsed.deps);
    const weight = function (r) { return r.breaking.length * 100 + r.advisories.length * 10 + r.deprecations.length; };
    rows.sort(function (a, b) { return weight(b) - weight(a); });
    out.replaceChildren(el("p", cut + rows.length + " dependencies read" + (parsed.skipped ? ", " + parsed.skipped + " skipped (unpinned, URLs or includes)" : "") + ".", "muted"));
    for (const row of rows.slice(0, 300)) renderRow(out, row);
  });
})();
