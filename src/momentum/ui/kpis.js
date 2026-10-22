// KPI tiles under the chart: label (with an optional help toggletip), the value
// and an optional delta. Mounted with st.components.v2 under a stable key, so a
// new symbol or range rolls the figures (numeric.js) instead of replacing them.
// All text is set with textContent.

const MM_SVG = "http://www.w3.org/2000/svg";
// Material "arrow_upward" / "info"-style glyphs, drawn inline (no icon font).
const MM_ARROW = "M12 19V5m0 0-6 6m6-6 6 6";
const MM_HELP =
  "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5v.01M12 13.5c0-1.5 2.25-1.75 2.25-3.75a2.25 2.25 0 0 0-4.5 0";

function mmKpiEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function mmIcon(d, className) {
  const svg = document.createElementNS(MM_SVG, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS(MM_SVG, "path");
  path.setAttribute("d", d);
  svg.appendChild(path);
  return svg;
}

function mmCloseTips(root, except) {
  for (const button of root.querySelectorAll("button.help[aria-expanded='true']")) {
    if (button !== except) button.setAttribute("aria-expanded", "false");
  }
}

function mmTile(list, index) {
  const tile = mmKpiEl("div", "kpi");
  const label = mmKpiEl("dt", "label");
  const text = mmKpiEl("span", "label-text");
  label.append(text);
  const value = mmKpiEl("dd", "value mm-num");
  const delta = mmKpiEl("dd", "delta");
  const deltaNum = mmKpiEl("span", "mm-num");
  delta.append(mmIcon(MM_ARROW, "arrow"), deltaNum);

  const help = mmKpiEl("button", "help");
  help.type = "button";
  help.setAttribute("aria-expanded", "false");
  const tipId = `mm-kpi-tip-${index}`;
  help.setAttribute("aria-describedby", tipId);
  help.append(mmIcon(MM_HELP, "help-icon"));
  const tip = mmKpiEl("span", "tip");
  tip.id = tipId;
  tip.setAttribute("role", "tooltip");
  help.addEventListener("click", () => {
    const open = help.getAttribute("aria-expanded") !== "true";
    mmCloseTips(list, help);
    help.setAttribute("aria-expanded", String(open));
  });
  help.addEventListener("blur", () => help.setAttribute("aria-expanded", "false"));
  label.append(help, tip);

  tile.append(label, value, delta);
  list.appendChild(tile);
  return { tile, text, help, tip, value, delta, deltaNum };
}

export default function (component) {
  const { data, parentElement } = component;
  const cards = (data && data.cards) || [];
  let view = parentElement.__mmKpis;
  if (!view) {
    const list = mmKpiEl("dl", "kpis");
    // Escape closes an open toggletip (WCAG 1.4.13: dismissible).
    list.addEventListener("keydown", (event) => {
      if (event.key === "Escape") mmCloseTips(list, null);
    });
    parentElement.appendChild(list);
    view = parentElement.__mmKpis = { list, tiles: [] };
  }
  while (view.tiles.length < cards.length) view.tiles.push(mmTile(view.list, view.tiles.length));
  while (view.tiles.length > cards.length) view.tiles.pop().tile.remove();

  cards.forEach((card, i) => {
    const t = view.tiles[i];
    t.text.textContent = card.label;
    t.help.hidden = !card.help;
    t.help.setAttribute("aria-label", `About ${card.label}`);
    t.tip.textContent = card.help || "";
    mmSetNumber(t.value, card.value, card.value_number);
    t.delta.hidden = !card.delta;
    t.delta.className = `delta ${card.tone || "off"}`;
    t.delta.dataset.arrow = card.arrow || "up";
    mmSetNumber(t.deltaNum, card.delta || "", card.delta_number);
  });
}
