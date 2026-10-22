// Watchlist rail: a list of real <button>s rendered from JSON data.
// Mounted with st.components.v2 (no iframe). All text is set with
// textContent, so names coming from Yahoo can never inject markup.
// Clicking a row sends a "select" trigger back to Python.

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

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  let list = parentElement.querySelector("ul.rail");
  if (!list) {
    list = el("ul", "rail");
    list.setAttribute("role", "list");
    parentElement.appendChild(list);
  }
  list.replaceChildren();

  for (const row of (data && data.rows) || []) {
    const button = el("button", "row");
    button.type = "button";
    button.setAttribute("aria-label", row.label);
    if (row.selected) button.setAttribute("aria-current", "true");

    const id = el("span", "id");
    id.append(el("span", "sym", row.symbol), el("span", "name", row.name));
    const quote = el("span", "quote");
    quote.append(el("span", "price", row.price), el("span", `chg ${row.direction}`, row.change));
    button.append(id, sparkline(row.spark), quote);
    button.addEventListener("click", () => setTriggerValue("select", row.symbol));

    const item = el("li");
    item.appendChild(button);
    list.appendChild(item);
  }
}
