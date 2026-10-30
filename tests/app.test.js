const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(resolve(__dirname, "..", "assets", "app.js"), "utf8");

async function dashboard() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: "",
        innerHTML: "",
        listeners: {},
        addEventListener(event, listener) {
          this.listeners[event] = listener;
        }
      });
    }
    return elements.get(id);
  }
  const buttons = ["checklists", "species"].map(key => ({
    ...element(key),
    dataset: { sort: key },
    parentElement: {
      setAttribute(name, value) {
        this[name] = value;
      }
    }
  }));
  const districts = [
    { district: "Mysuru", checklists: 12, species: 8, speciesList: [] },
    { district: "Mandya", checklists: 3, species: 20, speciesList: [] },
    { district: "Bagalkot", checklists: 12, species: 2, speciesList: [] }
  ];
  const errors = [];
  const context = vm.createContext({
    document: {
      getElementById: element,
      querySelector: element,
      querySelectorAll: () => buttons
    },
    Chart: function () {},
    console: { error: error => errors.push(error) },
    fetch: async path => ({
      ok: true,
      json: async () => path.includes("district_summary") ? districts
        : path.includes("daily_summary") ? [] : {}
    })
  });
  vm.runInContext(source, context);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(errors, []);
  return {
    buttons,
    districts,
    rows: () => [...element("#districtTable tbody").innerHTML.matchAll(/<td>([^<]+)<\/td>/g)]
      .filter((_, index) => index % 3 === 0)
      .map(match => match[1]),
    search(value) {
      const search = element("districtSearch");
      search.value = value;
      search.listeners.input({ target: search });
    },
    click(key) {
      buttons.find(button => button.dataset.sort === key).listeners.click();
    },
    body: () => element("#districtTable tbody").innerHTML
  };
}

test("defaults to numeric checklist descending with alphabetical ties", async () => {
  const app = await dashboard();
  assert.deepEqual(app.rows(), ["Bagalkot", "Mysuru", "Mandya"]);
  assert.equal(app.buttons[0].parentElement["aria-sort"], "descending");
  assert.equal(app.buttons[1].parentElement["aria-sort"], "none");
  assert.deepEqual(app.districts.map(d => d.district), ["Mysuru", "Mandya", "Bagalkot"]);
});

test("toggles both count columns and resets a newly selected column to descending", async () => {
  const app = await dashboard();
  app.click("checklists");
  assert.deepEqual(app.rows(), ["Mandya", "Bagalkot", "Mysuru"]);
  assert.equal(app.buttons[0].parentElement["aria-sort"], "ascending");
  app.click("checklists");
  assert.deepEqual(app.rows(), ["Bagalkot", "Mysuru", "Mandya"]);
  app.click("species");
  assert.deepEqual(app.rows(), ["Mandya", "Mysuru", "Bagalkot"]);
  assert.equal(app.buttons[0].parentElement["aria-sort"], "none");
  assert.equal(app.buttons[1].parentElement["aria-sort"], "descending");
  app.click("species");
  assert.deepEqual(app.rows(), ["Bagalkot", "Mysuru", "Mandya"]);
  assert.equal(app.buttons[1].parentElement["aria-sort"], "ascending");
  app.click("checklists");
  assert.deepEqual(app.rows(), ["Bagalkot", "Mysuru", "Mandya"]);
});

test("sorting preserves the search and searching preserves the sort", async () => {
  const app = await dashboard();
  app.search("  M  ");
  assert.deepEqual(app.rows(), ["Mysuru", "Mandya"]);
  app.click("species");
  assert.deepEqual(app.rows(), ["Mandya", "Mysuru"]);
  app.click("species");
  assert.deepEqual(app.rows(), ["Mysuru", "Mandya"]);
  app.search("");
  assert.deepEqual(app.rows(), ["Bagalkot", "Mysuru", "Mandya"]);
  app.search("no such district");
  app.click("checklists");
  assert.deepEqual(app.rows(), []);
  assert.match(app.body(), /No matching district/);
});
