// Minimal Lean 4 highlighter for <code class="language-lean"> blocks.
// No dependencies; covers the keywords, sorts and notation these snippets use.
(() => {
  const KEYWORDS = new Set([
    'theorem', 'lemma', 'def', 'abbrev', 'instance', 'structure', 'class', 'inductive',
    'example', 'axiom', 'import', 'open', 'namespace', 'section', 'end', 'variable',
    'where', 'with', 'match', 'fun', 'by', 'have', 'show', 'let', 'if', 'then', 'else',
    'do', 'at', 'from', 'calc', 'noncomputable', 'private', 'protected',
  ]);
  const DECLS = new Set(['theorem', 'lemma', 'def', 'abbrev', 'instance', 'structure',
    'class', 'inductive', 'axiom']);
  const SORTS = new Set(['Prop', 'Type', 'Sort']);
  const TYPES = new Set(['ℂ', 'ℕ', 'ℝ', 'ℤ', 'ℚ']);

  // Order matters: comments and strings first, then words, numbers, symbols.
  const TOKEN = new RegExp([
    String.raw`(?<comment>--[^\n]*|/-[\s\S]*?-/)`,
    String.raw`(?<string>"(?:[^"\\]|\\.)*")`,
    String.raw`(?<word>[A-Za-z_α-ωΑ-Ωℂℕℝℤℚ][\w.'α-ωΑ-Ω!?₀-₉]*)`,
    String.raw`(?<number>\d+(?:\.\d+)?)`,
    String.raw`(?<binder>[∀∃λ]|fun\b)`,
    String.raw`(?<op>:=|[→↔¬∧∨≠≤≥<>=×∘⁻¹|])`,
  ].join('|'), 'gu');

  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const span = (cls, s) => `<span class="tok-${cls}">${esc(s)}</span>`;

  function highlight(src) {
    let out = '', last = 0, expectName = false;
    for (const m of src.matchAll(TOKEN)) {
      out += esc(src.slice(last, m.index));
      last = m.index + m[0].length;
      const g = m.groups, t = m[0];
      if (g.comment) out += span('comment', t);
      else if (g.string) out += span('string', t);
      else if (g.word) {
        if (expectName) { out += span('name', t); expectName = false; }
        else if (KEYWORDS.has(t)) { out += span('keyword', t); expectName = DECLS.has(t); }
        else if (SORTS.has(t) || TYPES.has(t)) out += span('sort', t);
        // Capitalized constants (NeZero, DirichletCharacter.LFunction), but not
        // one-letter variables (N) or projections off a variable (χ.IsPrimitive).
        else if (/^[A-Z][\w']/.test(t)) out += span('type', t);
        else out += esc(t);
      }
      else if (g.number) out += span('number', t);
      else if (g.binder) out += span('keyword', t);
      else if (g.op) out += span('op', t);
    }
    return out + esc(src.slice(last));
  }

  for (const el of document.querySelectorAll('code.language-lean')) {
    el.innerHTML = highlight(el.textContent);
  }
})();
