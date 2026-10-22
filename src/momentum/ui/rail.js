// Watchlist rail: a list of real <button>s rendered from JSON data.
// Mounted with st.components.v2 (no iframe). All text is set with
// textContent, so names coming from Yahoo can never inject markup.
// Clicking a row (or Enter/Space on it) sends a "select" trigger to Python.
//
// Keyboard: Tab reaches each row; Up/Down/Home/End move between rows. Every
// rerun rebuilds the rows, so the focused row is remembered by symbol and
// focused again, rather than dropping focus to <body>.

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function sparkline(d) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 56 24");
  svg.setAttribute("width", "56");
  svg.setAttribute("height", "24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (d) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

function focusedSymbol(root) {
  const active = root.activeElement;
  return active && active.dataset ? active.dataset.symbol || null : null;
}

function onKeydown(event) {
  const buttons = [...event.currentTarget.querySelectorAll("button.row")];
  const index = buttons.indexOf(event.target);
  if (index < 0) return;
  const target = {
    ArrowDown: Math.min(index + 1, buttons.length - 1),
    ArrowUp: Math.max(index - 1, 0),
    Home: 0,
    End: buttons.length - 1,
  }[event.key];
  if (target === undefined) return;
  event.preventDefault();
  buttons[target].focus();
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const root = parentElement.getRootNode();
  let list = parentElement.querySelector("ul.rail");
  if (!list) {
    list = el("ul", "rail");
    list.setAttribute("role", "list");
    list.addEventListener("keydown", onKeydown);
    parentElement.appendChild(list);
  }
  const keep = focusedSymbol(root) || window.__momentumRailFocus || null;
  window.__momentumRailFocus = null;
  list.replaceChildren();

  for (const row of (data && data.rows) || []) {
    const button = el("button", "row");
    button.type = "button";
    button.dataset.symbol = row.symbol;
    button.setAttribute("aria-label", row.label);
    if (row.selected) button.setAttribute("aria-current", "true");

    const id = el("span", "id");
    id.append(el("span", "sym", row.symbol), el("span", "name", row.name));
    const quote = el("span", "quote");
    quote.append(el("span", "price", row.price));
    if (row.change) quote.append(el("span", `chg ${row.direction}`, row.change));
    button.append(id, sparkline(row.spark), quote);
    button.addEventListener("click", () => {
      // The rerun may remount this component; remember where focus was.
      window.__momentumRailFocus = row.symbol;
      setTriggerValue("select", row.symbol);
    });

    const item = el("li");
    item.appendChild(button);
    list.appendChild(item);
  }

  if (keep) {
    const again = [...list.querySelectorAll("button.row")].find((b) => b.dataset.symbol === keep);
    if (again) again.focus({ preventScroll: true });
  }
}
