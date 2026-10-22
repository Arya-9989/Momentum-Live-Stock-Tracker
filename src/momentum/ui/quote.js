// Quote header: symbol line, instrument name, the hero price and the day change.
// Mounted with st.components.v2 (no iframe) under a stable key, so the same DOM
// stays across reruns and a new symbol's figures roll in (numeric.js). Every
// string is set with textContent: names from Yahoo never become markup.

function mmEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function mmBuildHeader(parent) {
  const header = mmEl("header", "mm-quote");
  const meta = mmEl("p", "mm-meta");
  const name = mmEl("h1", "mm-name");
  const price = mmEl("p", "mm-price");
  const priceNum = mmEl("span", "mm-num");
  const asOf = mmEl("time", "mm-asof");
  price.append(priceNum, asOf);
  const change = mmEl("p", "mm-change flat");
  const changeNum = mmEl("span", "mm-num");
  const when = mmEl("span", "mm-when", "Today");
  change.append(changeNum, when);
  header.append(meta, name, price, change);
  parent.appendChild(header);
  return { header, meta, name, priceNum, asOf, change, changeNum, when };
}

function mmMeta(node, data) {
  const parts = [mmEl("span", "mm-sym", data.symbol)];
  for (const part of data.meta || []) {
    const opt = part.optional ? " mm-opt" : "";
    const sep = mmEl("span", `mm-sep${opt}`, "\u00b7");
    sep.setAttribute("aria-hidden", "true");
    parts.push(sep, mmEl("span", `mm-part${opt}`, part.text));
  }
  if (data.state) {
    const state = mmEl("span", "mm-state", data.state.label);
    state.dataset.state = data.state.code;
    parts.push(state);
  }
  node.replaceChildren(...parts);
}

export default function (component) {
  const { data, parentElement } = component;
  if (!data) return;
  const view = parentElement.__mmQuote || (parentElement.__mmQuote = mmBuildHeader(parentElement));

  mmMeta(view.meta, data);
  view.name.textContent = data.name;
  view.name.title = data.name;
  mmSetNumber(view.priceNum, data.price, data.price_value);

  if (data.as_of) {
    view.asOf.dateTime = data.as_of.iso;
    view.asOf.textContent = data.as_of.label;
  } else {
    view.asOf.removeAttribute("datetime");
    view.asOf.textContent = "";
  }

  const change = data.change;
  view.change.className = `mm-change ${change ? change.direction : "flat"}`;
  view.when.hidden = !change;
  // A non-breaking space keeps the line's height when there is no change to show.
  mmSetNumber(view.changeNum, change ? change.text : "\u00a0", change ? change.value : null);
}
