"use strict";
// shell-parse.js - the shell lexing the unattended guard reads a Bash command through.
// No policy lives here: it only turns a command string into simple commands.
const base = (t) => t.slice(t.lastIndexOf("/") + 1);
const ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=/;
const KEYWORDS = new Set(["if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time", "fi", "done", "esac"]);
// Wrappers that run a command after their own options. args: options with a value;
// pos: positional operands to skip before the command (timeout DURATION).
const w0 = (pos, ...args) => ({ args, pos });
const WRAP = {
  env: w0(0, "-u", "-C", "-S", "--unset", "--chdir", "--split-string"),
  sudo: w0(0, "-u", "-g", "-h", "-p", "-C", "-r", "-t", "-U", "-D", "-R", "--user", "--group"),
  doas: w0(0, "-u", "-C"), timeout: w0(1, "-s", "-k", "--signal", "--kill-after"),
  nice: w0(0, "-n", "--adjustment"), ionice: w0(0, "-c", "-n", "-p", "-P", "-u"),
  stdbuf: w0(0, "-i", "-o", "-e"), setsid: w0(0), nohup: w0(0), command: w0(0), exec: w0(0, "-a"),
  builtin: w0(0), unbuffer: w0(0), chronic: w0(0), flock: w0(1, "-w", "-E", "-c"),
  xargs: w0(0, "-I", "-n", "-P", "-d", "-L", "-E", "-s", "-a", "-l", "-i"),
};

// Take heredoc bodies out of the text. A body is returned for analysis only when its
// operator line names a shell (bash <<EOT, cat <<EOT | bash). Quote-aware; <<< is not one.
const SHELL_LINE = /(^|[\s|;&(`/])(bash|sh|zsh|dash|ksh)(\s|$)/;
const extractHeredocs = (s) => {
  let out = "", quote = null, pending = [], lineStart = 0;
  const bodies = [], subs = [];
  for (let i = 0; i < s.length;) {
    const c = s[i];
    // Inside $( ) the quote state starts afresh.
    if (c === "$" && s[i + 1] === "(" && quote !== "'") { subs.push({ q: quote, d: 0 }); quote = null; out += "$("; i += 2; continue; }
    if (quote === null && subs.length && (c === "(" || c === ")")) {
      const top = subs[subs.length - 1];
      if (c === "(") top.d += 1; else if (top.d > 0) top.d -= 1; else quote = subs.pop().q;
      out += c; i += 1; continue;
    }
    if (quote) {
      if (c === "\\" && quote === "\"" && i + 1 < s.length) { out += s.slice(i, i + 2); i += 2; continue; }
      if (c === quote) quote = null;
      out += c; i += 1; continue;
    }
    if (c === "\"" || c === "'") { quote = c; out += c; i += 1; continue; }
    if (c === "\\") { out += s.slice(i, i + 2); i += 2; continue; }
    if (c === "<" && s[i + 1] === "<" && s[i + 2] !== "<" && s[i - 1] !== "<") {
      let j = i + 2, delim = "";
      if (s[j] === "-") j += 1;
      while (s[j] === " " || s[j] === "\t") j += 1;
      if (s[j] === "\"" || s[j] === "'") {
        const q = s[j]; j += 1;
        while (j < s.length && s[j] !== q) { delim += s[j]; j += 1; }
        j += 1;
      } else while (j < s.length && !/[\s;|&<>()]/.test(s[j])) { delim += s[j]; j += 1; }
      if (delim) { pending.push(delim); out += s.slice(i, j); i = j; continue; }
    }
    if (c === "\n" && pending.length) {
      const line = out.slice(lineStart);
      out += "\n"; i += 1;
      for (const delim of pending) {
        const body = [];
        while (i < s.length) {
          let e = s.indexOf("\n", i); if (e < 0) e = s.length;
          const l = s.slice(i, e); i = Math.min(e + 1, s.length);
          if (l.trim() === delim) break;
          body.push(l);
        }
        if (SHELL_LINE.test(line)) bodies.push(body.join("\n"));
      }
      pending = []; lineStart = out.length; continue;
    }
    if (c === "\n") lineStart = out.length + 1;
    out += c; i += 1;
  }
  return { text: out, bodies };
};

// Split into simple commands. A token is {t, q}, {redir: true} (the next token is the
// file target) or {here: true} (the next token is the here-string). seg.pipedFrom is
// the segment before a pipe, seg.bg marks a detached segment.
const parse = (s) => {
  const segs = [];
  let toks = [], cur = "", q = false, quote = null, has = false, from = null, last = null;
  const endTok = () => { if (has) toks.push({ t: cur, q }); cur = ""; q = false; has = false; };
  const endSeg = (pipe) => {
    endTok();
    if (toks.length) { toks.pipedFrom = from; segs.push(toks); last = toks; }
    toks = []; from = pipe ? last : null;
  };
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (quote) {
      if (c === quote) quote = null;
      else if (c === "\\" && quote === "\"" && i + 1 < s.length) { cur += s[i + 1]; i += 1; }
      else cur += c;
      continue;
    }
    if (c === "\"" || c === "'") { quote = c; q = true; has = true; continue; }
    if (c === "\\" && i + 1 < s.length) { cur += s[i + 1]; has = true; i += 1; continue; }
    if (/\s/.test(c) && c !== "\n") { endTok(); continue; }
    if (c === "\n" || c === ";" || c === "(" || c === ")") { endSeg(false); continue; }
    if (c === "|") { endSeg(true); if (s[i + 1] === "|") { i += 1; from = null; } else if (s[i + 1] === "&") i += 1; continue; }
    if (c === ">") {
      if (has && !q && /^\d+$/.test(cur)) { cur = ""; has = false; } else endTok();   // 2>f: a descriptor
      let j = i + 1;
      if (s[j] === ">" || s[j] === "|") j += 1;
      if (s[j] === "&") { j += 1; while (/[0-9-]/.test(s[j] || "")) j += 1; }       // >&2: no file
      else toks.push({ redir: true });
      i = j - 1; continue;
    }
    if (c === "<") {
      endTok();
      let j = i + 1;
      while (s[j] === "<") j += 1;
      if (j - i === 3) toks.push({ here: true });
      i = j - 1; continue;
    }
    if (c === "&") {
      if (s[i + 1] === ">") { cur = ""; has = false; continue; }                      // &>file
      if (s[i + 1] === "&") { endSeg(false); i += 1; continue; }
      endTok(); if (toks.length) toks.bg = true;
      endSeg(false); continue;
    }
    cur += c; has = true;
  }
  endSeg(false);
  return segs;
};

// $( ... ) and backticks. A single-quoted region and an escaped backtick are text.
const nested = (s) => {
  const out = [];
  let dq = false;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (c === "\\") { i += 1; continue; }
    if (!dq && c === "'") { const e = s.indexOf("'", i + 1); if (e < 0) break; i = e; continue; }
    if (c === "\"") { dq = !dq; continue; }
    if (c === "`") {
      let j = i + 1;
      while (j < s.length && s[j] !== "`") j += s[j] === "\\" ? 2 : 1;
      out.push(s.slice(i + 1, j)); i = j; continue;
    }
    if (c === "$" && s[i + 1] === "(") {
      let depth = 1, j = i + 2;
      while (j < s.length && depth > 0) {
        if (s[j] === "\\") { j += 2; continue; }
        if (s[j] === "(") depth += 1; else if (s[j] === ")") depth -= 1;
        j += 1;
      }
      out.push(s.slice(i + 2, j - 1)); i = j - 1;
    }
  }
  return out;
};

// Drop control keywords, assignments and wrapper options so the real command is the
// head. The result carries .detached when a nohup or setsid wrapper was stripped.
const unwrap = (words) => {
  let w = words.slice(), detached = false;
  for (let guard = 0; guard < 20 && w.length; guard += 1) {
    if (KEYWORDS.has(w[0]) || ASSIGN.test(w[0])) { w = w.slice(1); continue; }
    const name = base(w[0]), spec = WRAP[name];
    if (!spec) break;
    if (name === "nohup" || name === "setsid") detached = true;
    let i = 1, skip = spec.pos;
    while (i < w.length) {
      const a = w[i];
      if (a.startsWith("-") && a !== "-") { i += spec.args.includes(a) ? 2 : 1; continue; }
      if (ASSIGN.test(a) && name === "env") { i += 1; continue; }
      if (skip > 0) { skip -= 1; i += 1; continue; }
      break;
    }
    w = w.slice(i);
  }
  w.detached = detached;
  return w;
};

module.exports = { base, extractHeredocs, parse, nested, unwrap };
