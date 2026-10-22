// Number transitions shared by the quote header and the KPI tiles (prepended to
// their component modules by components.py, so it defines no exports).
//
// It follows Apple's numeric text transition: when a figure changes, only the
// characters that differ roll, the new one in and the old one out, upwards
// when the value rose and downwards when it fell. Unchanged characters keep
// still. Only real values are ever shown (the old one, then the new one); there
// is no count through made-up intermediate numbers.
//
// The node always ends as a single text node equal to the formatted value from
// Python, so the static and the animated result are identical. With reduced
// motion, on the first render, or without the Web Animations API, the value
// is set immediately. Only transform and opacity are animated, and the slot
// widths are those of the new value, so nothing outside the number moves.

const MM_MOTION = typeof matchMedia === "function"
  ? matchMedia("(prefers-reduced-motion: reduce)")
  : null;
const MM_ENTER_EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
const MM_EXIT_EASE = "cubic-bezier(0.4, 0, 0.6, 1)";
const MM_ROLL_MS = 420;
const MM_EXIT_MS = 180;
const MM_HANDOFF_MS = 40; // the old glyph is mostly gone before the new one shows
const MM_STAGGER_MS = 18;
const MM_STAGGER_MAX_MS = 120;

function mmReducedMotion() {
  return Boolean(MM_MOTION && MM_MOTION.matches);
}

// Right-aligned character diff: which positions of `next` differ from `prev`.
// Figures line up on their last digit, so "$190.00" -> "$1,234.56" keeps the
// cents in place while the integer part grows to the left.
function mmChangedSlots(prev, next) {
  const offset = next.length - prev.length;
  return [...next].map((ch, i) => {
    const j = i - offset;
    return j < 0 || prev[j] !== ch ? (j < 0 ? "" : prev[j]) : null;
  });
}

function mmSpan(className, text) {
  const node = document.createElement("span");
  node.className = className;
  node.textContent = text;
  return node;
}

/**
 * Show `next` (a formatted string) in `node`, rolling the characters that changed.
 * `value` is the number behind it (or null); it only decides the roll direction.
 * Returns a promise that resolves once the final text is in place.
 */
function mmSetNumber(node, next, value) {
  const prev = node.__mmText;
  const prevValue = node.__mmValue;
  node.__mmText = next;
  node.__mmValue = value;
  const token = (node.__mmToken = (node.__mmToken || 0) + 1);

  const animate = typeof node.animate === "function";
  if (prev === undefined || prev === next || mmReducedMotion() || !animate) {
    node.textContent = next;
    return Promise.resolve();
  }

  const up = !(Number.isFinite(value) && Number.isFinite(prevValue) && value < prevValue);
  const shift = up ? 0.6 : -0.6; // em; enter from below when rising, from above when falling
  const slots = mmChangedSlots(prev, next);

  const roll = mmSpan("mm-roll", "");
  roll.setAttribute("aria-hidden", "true");
  const animations = [];
  let changed = 0;
  [...next].forEach((ch, i) => {
    const old = slots[i];
    if (old === null) {
      roll.append(ch);
      return;
    }
    const slot = mmSpan("mm-slot", "");
    const incoming = mmSpan("mm-in", ch);
    slot.append(incoming);
    const delay = Math.min(changed * MM_STAGGER_MS, MM_STAGGER_MAX_MS);
    changed += 1;
    animations.push(
      incoming.animate(
        [
          { transform: `translateY(${shift}em)`, opacity: 0 },
          { transform: "translateY(0)", opacity: 1 },
        ],
        { duration: MM_ROLL_MS, delay: delay + MM_HANDOFF_MS, easing: MM_ENTER_EASE, fill: "backwards" },
      ),
    );
    if (old) {
      const outgoing = mmSpan("mm-out", old);
      slot.append(outgoing);
      animations.push(
        outgoing.animate(
          [
            { transform: "translateY(0)", opacity: 1 },
            { transform: `translateY(${-shift}em)`, opacity: 0 },
          ],
          { duration: MM_EXIT_MS, delay, easing: MM_EXIT_EASE, fill: "forwards" },
        ),
      );
    }
    roll.append(slot);
  });

  // Assistive tech reads the final value once; the rolling glyphs are hidden from it.
  node.replaceChildren(mmSpan("mm-sr", next), roll);
  return Promise.all(animations.map((a) => a.finished))
    .catch(() => undefined)
    .then(() => {
      if (node.__mmToken === token) node.textContent = next;
    });
}
