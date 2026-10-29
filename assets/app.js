let districtSummary = [];
let karnatakaSummary = {};
let dailySummary = [];
let dailyChart;

function renderStats() {
  document.getElementById("totalChecklists").textContent =
    karnatakaSummary.checklists ?? 0;

  document.getElementById("totalSpecies").textContent =
    karnatakaSummary.species ?? 0;

  document.getElementById("districtsCovered").textContent =
    karnatakaSummary.districtsCovered ?? 0;

  document.getElementById("lastUpdated").textContent =
    `Updated: ${karnatakaSummary.lastUpdated ?? "—"}`;

  const coverageWarning = document.getElementById("coverageWarning");
  coverageWarning.textContent = karnatakaSummary.coverageWarning || "";
  coverageWarning.hidden = !karnatakaSummary.coverageWarning;
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

    renderStats();
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