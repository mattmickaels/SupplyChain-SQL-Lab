(function () {
  "use strict";
  function element(tag, text, className) {
    var node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  document.querySelectorAll("[data-query-lab]").forEach(function (lab) {
    var plants = [["AU01", "Austin", "US"], ["SC01", "Santa Clara", "US"], ["SG01", "Singapore", "SG"]];
    var examples = {
      all: { columns: ["plant_code", "name", "country"], rows: plants,
        sql: "SELECT plant_code, name, country FROM plant ORDER BY plant_code;",
        explanation: "FROM chooses the table. SELECT names the columns. ORDER BY makes the row order predictable." },
      columns: { columns: ["name", "country"], rows: plants.map(function (row) { return row.slice(1); }),
        sql: "SELECT name, country FROM plant ORDER BY plant_code;",
        explanation: "SELECT changes the columns in your result. All three plant records are still here." },
      filter: { columns: ["name", "country"], rows: plants.filter(function (row) { return row[2] === "US"; }).map(function (row) { return row.slice(1); }),
        sql: "SELECT name, country FROM plant WHERE country = 'US' ORDER BY plant_code;",
        explanation: "WHERE keeps only rows that match. Singapore is excluded from this result, but stays in the source table." },
      group: { columns: ["country", "plant_count"], rows: [["SG", 1], ["US", 2]],
        sql: "SELECT country, COUNT(*) AS plant_count FROM plant GROUP BY country ORDER BY country;",
        explanation: "GROUP BY produces one row per country. COUNT(*) counts the plants in each group: one in SG and two in US." }
    };
    function paint(step) {
      var example = examples[step];
      lab.querySelectorAll("[data-query-step]").forEach(function (button) { button.setAttribute("aria-pressed", String(button.dataset.queryStep === step)); });
      lab.querySelectorAll("[data-country]").forEach(function (row) {
        row.classList.toggle("example-excluded", step === "filter" && row.dataset.country !== "US");
        row.classList.toggle("example-grouped", step === "group");
      });
      var table = element("table", undefined, "grid");
      var head = table.createTHead().insertRow();
      example.columns.forEach(function (name) { var th = element("th", name); th.scope = "col"; head.appendChild(th); });
      var body = table.createTBody();
      example.rows.forEach(function (row) { var tr = body.insertRow(); row.forEach(function (value) { tr.appendChild(element("td", String(value))); }); });
      lab.querySelector("[data-example-result]").replaceChildren(table);
      lab.querySelector("[data-example-count]").textContent = example.rows.length + " rows · " + example.columns.length + " columns";
      lab.querySelector("[data-example-sql]").textContent = example.sql;
      lab.querySelector("[data-example-explanation]").textContent = example.explanation;
    }
    lab.querySelectorAll("[data-query-step]").forEach(function (button) { button.addEventListener("click", function () { paint(button.dataset.queryStep); }); });
    paint("all");
  });

  document.querySelectorAll("[data-schema-map]").forEach(function (map) {
    var tables = JSON.parse(map.querySelector("[data-map-data]").textContent);
    var select = map.querySelector("[data-map-select]");
    function connection(tableName, source, column, reference) {
      var card = element("div", undefined, "map-connection");
      var button = element("button", tableName);
      button.type = "button";
      button.setAttribute("aria-label", "Focus table " + tableName + ", linked by " + column);
      button.addEventListener("click", function () { select.value = tableName; paint(); select.focus(); });
      card.append(button, element("code", source + "." + column + " → " + reference));
      return card;
    }
    function paint() {
      var selected = tables.find(function (table) { return table.name === select.value; });
      if (!selected) return;
      var focus = map.querySelector("[data-map-focus]");
      var link = element("a", "Open table definition →");
      link.href = "/schema/" + encodeURIComponent(selected.name);
      focus.replaceChildren(element("span", "FOCUS TABLE", "data-badge"), element("h3", selected.name), element("p", selected.description), element("code", "PK · " + selected.key.join(" + ")), element("small", selected.columns + " columns"), link);
      var parents = map.querySelector("[data-map-parents]");
      var children = map.querySelector("[data-map-children]");
      parents.replaceChildren(); children.replaceChildren();
      selected.links.forEach(function (fk) { parents.appendChild(connection(fk[1], selected.name, fk[0], fk[1] + "." + fk[2])); });
      var incoming = 0;
      tables.forEach(function (table) { table.links.forEach(function (fk) {
        if (fk[1] === selected.name) {
          incoming += 1;
          children.appendChild(connection(table.name, table.name, fk[0], selected.name + "." + fk[2]));
        }
      }); });
      if (!selected.links.length) parents.appendChild(element("p", "No outgoing key references.", "map-empty"));
      if (!incoming) children.appendChild(element("p", "No incoming key references.", "map-empty"));
      focus.dataset.parents = String(selected.links.length);
      focus.dataset.children = String(incoming);
      map.querySelector("[data-map-status]").textContent = selected.name + ": " + selected.links.length + " outgoing and " + incoming + " incoming key references.";
    }
    select.addEventListener("change", paint);
    paint();
  });
})();
