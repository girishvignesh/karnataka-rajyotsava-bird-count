const KARNATAKA_GEOJSON_URL =
  "https://raw.githubusercontent.com/datameet/maps/master/Districts/karnataka.geojson";

let districtSummary = [];
let karnatakaSummary = {};
let dailySummary = [];
let map;
let geojsonLayer;
let dailyChart;

const districtNameMap = {
  "bangalore urban": "Bengaluru Urban",
  "bengaluru urban": "Bengaluru Urban",
  "bangalore rural": "Bengaluru Rural",
  "bengaluru rural": "Bengaluru Rural",
  "belgaum": "Belagavi",
  "bellary": "Ballari",
  "bijapur": "Vijayapura",
  "mysore": "Mysuru",
  "tumkur": "Tumakuru",
  "shimoga": "Shivamogga",
  "chikmagalur": "Chikkamagaluru",
  "coorg": "Kodagu"
};

function normalizeName(name) {
  const key = String(name || "").trim().toLowerCase();
  return districtNameMap[key] || String(name || "").trim();
}

function getColor(checklists, max) {
  if (!checklists) return "#f1f5f9";

  const ratio = max ? checklists / max : 0;

  if (ratio > 0.75) return "#14532d";
  if (ratio > 0.5) return "#166534";
  if (ratio > 0.25) return "#22c55e";
  return "#86efac";
}

function initMap() {
  map = L.map("map").setView([15.3, 75.7], 7);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);
}

function renderMap() {
  const max = Math.max(...districtSummary.map(d => d.checklists), 1);

  const lookup = new Map(
    districtSummary.map(d => [normalizeName(d.district).toLowerCase(), d])
  );

  if (geojsonLayer) {
    map.removeLayer(geojsonLayer);
  }

  fetch(KARNATAKA_GEOJSON_URL)
    .then(response => response.json())
    .then(geojson => {
      geojsonLayer = L.geoJSON(geojson, {
        style: feature => {
          const name = normalizeName(
            feature.properties.DISTRICT ||
            feature.properties.district ||
            feature.properties.NAME_2 ||
            feature.properties.name
          );

          const data = lookup.get(name.toLowerCase());
          const count = data ? data.checklists : 0;

          return {
            fillColor: getColor(count, max),
            fillOpacity: 0.8,
            color: "#ffffff",
            weight: 1
          };
        },
        onEachFeature: (feature, layer) => {
          const name = normalizeName(
            feature.properties.DISTRICT ||
            feature.properties.district ||
            feature.properties.NAME_2 ||
            feature.properties.name
          );

          const data = lookup.get(name.toLowerCase());

          layer.bindPopup(
            `<strong>${name}</strong><br>
             Checklists: ${data ? data.checklists : 0}<br>
             Species: ${data ? data.species : 0}`
          );

          layer.on("click", () => {
            document.getElementById("districtSearch").value = name;
            renderDistrictTable(name);
          });
        }
      }).addTo(map);
    });
}

function renderStats() {
  document.getElementById("totalChecklists").textContent =
    karnatakaSummary.checklists ?? 0;

  document.getElementById("totalSpecies").textContent =
    karnatakaSummary.species ?? 0;

  document.getElementById("districtsCovered").textContent =
    karnatakaSummary.districtsCovered ?? 0;

  document.getElementById("lastUpdated").textContent =
    `Updated: ${karnatakaSummary.lastUpdated ?? "—"}`;
}

function renderDailyChart() {
  const ctx = document.getElementById("dailyChart");

  if (dailyChart) {
    dailyChart.destroy();
  }

  dailyChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: dailySummary.map(d => d.date),
      datasets: [
        {
          label: "Checklists submitted",
          data: dailySummary.map(d => d.checklists),
          backgroundColor: "#166534",
          borderRadius: 6
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      }
    }
  });
}

function renderDistrictTable(filter = "") {
  const tbody = document.querySelector("#districtTable tbody");
  const query = filter.trim().toLowerCase();

  const rows = districtSummary
    .filter(d => d.district.toLowerCase().includes(query))
    .sort((a, b) => b.checklists - a.checklists)
    .map(
      d => `
        <tr>
          <td>${d.district}</td>
          <td>${d.checklists}</td>
          <td>${d.species}</td>
        </tr>`
    );

  tbody.innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="3">No matching district.</td></tr>`;
}

function renderSpeciesTable(filter = "") {
  const speciesDistricts = new Map();

  districtSummary.forEach(district => {
    district.speciesList.forEach(species => {
      if (!speciesDistricts.has(species)) {
        speciesDistricts.set(species, new Set());
      }

      speciesDistricts.get(species).add(district.district);
    });
  });

  const tbody = document.querySelector("#speciesTable tbody");
  const query = filter.trim().toLowerCase();

  const rows = [...speciesDistricts.entries()]
    .filter(([species]) => species.toLowerCase().includes(query))
    .sort((a, b) => b[1].size - a[1].size || a[0].localeCompare(b[0]))
    .map(
      ([species, districts]) => `
        <tr>
          <td>${species}</td>
          <td>${districts.size}</td>
        </tr>`
    );

  tbody.innerHTML = rows.length
    ? rows.join("")
    : `<tr><td colspan="2">No matching species.</td></tr>`;
}

async function loadJSON(path) {
  const response = await fetch(path);

  if (!response.ok) {
    throw new Error(`Could not load ${path}`);
  }

  return response.json();
}

async function init() {
  try {
    [districtSummary, karnatakaSummary, dailySummary] = await Promise.all([
      loadJSON("data/district_summary.json"),
      loadJSON("data/karnataka_summary.json"),
      loadJSON("data/daily_summary.json")
    ]);

    initMap();
    renderStats();
    renderMap();
    renderDailyChart();
    renderDistrictTable();
    renderSpeciesTable();

    document.getElementById("districtSearch").addEventListener("input", e => {
      renderDistrictTable(e.target.value);
    });

    document.getElementById("speciesSearch").addEventListener("input", e => {
      renderSpeciesTable(e.target.value);
    });
  } catch (error) {
    console.error(error);
    document.getElementById("lastUpdated").textContent =
      "Unable to load dashboard data.";
  }
}

init();