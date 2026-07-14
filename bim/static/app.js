// State Variables
let materialsDB = {};
let currentQuantities = {};
let predictedMetrics = {};

// Unit System Variables
let currentUnitSystem = 'metric';
const CONV_SQM_TO_SQFT = 10.76391;
const CONV_CUM_TO_CUFT = 35.31467;

// Charts Instances
let costChart = null;
let carbonChart = null;
let altCostChart = null;
let altCarbonChart = null;

// U-value mapping tables for alternative design options
const WALL_INSULATION_U_MAPPING = {
    // Wall type -> Insulation type -> [Wall U-value, Roof U-value]
    "wall_concrete": {
        "none": [1.3084, 1.0384],
        "insulation_25": [0.1442, 0.1386],
        "insulation_50": [0.0763, 0.0751],
        "insulation_75": [0.0519, 0.0513],
        "insulation_100": [0.0393, 0.0390]
    },
    "wall_brick": {
        "none": [0.5850, 0.9570],
        "insulation_25": [0.1270, 0.1401],
        "insulation_50": [0.0712, 0.0751],
        "insulation_75": [0.0519, 0.0513],
        "insulation_100": [0.0379, 0.0513]
    },
    "wall_aac": {
        "none": [0.2395, 1.0384],
        "insulation_25": [0.0960, 0.1401],
        "insulation_50": [0.0603, 0.0751],
        "insulation_75": [0.0439, 0.0513],
        "insulation_100": [0.0346, 0.0390]
    }
};

const GLAZING_U_MAPPING = {
    "glazing_single": 0.6496,
    "glazing_double": 0.5031,
    "glazing_triple": 0.2563
};

// -------------------------------------------------------------
// APP INITIALIZATION
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", async () => {
    // 1. Setup Navigation Tabs
    setupTabs();

    // 2. Fetch Database Defaults
    await fetchDatabase();

    // 3. Setup Range Sliders & Inputs
    setupDashboardControls();

    // 4. Load Initial Estimates
    recalculateBIMEstimates();

    // 5. Setup Revit CSV Upload Dropzone
    setupCSVUpload();

    // 6. Setup Design Option Compare Buttons
    document.getElementById("btn-compare-options").addEventListener("click", runAlternativesAnalysis);

    // 7. Setup Live Sync Pull Button
    document.getElementById("btn-pull-revit-api").addEventListener("click", pullRevitLiveSync);

    // 8. Setup Unit System Toggle
    const unitSelect = document.getElementById("select-unit-system");
    if (unitSelect) {
        unitSelect.addEventListener("change", (e) => {
            const oldSystem = currentUnitSystem;
            currentUnitSystem = e.target.value;
            updateUILabelsAndQuantities(oldSystem, currentUnitSystem);
            renderDatabaseEditor();
            recalculateBIMEstimates();
        });
    }
});

// -------------------------------------------------------------
// TAB NAVIGATION
// -------------------------------------------------------------
function setupTabs() {
    const navItems = document.querySelectorAll(".nav-item");
    const panels = document.querySelectorAll(".tab-panel");

    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const tabId = item.getAttribute("data-tab");

            navItems.forEach(nav => nav.classList.remove("active"));
            panels.forEach(panel => panel.classList.remove("active"));

            item.classList.add("active");
            document.getElementById(`tab-${tabId}`).classList.add("active");
            
            // Re-render charts on tab switch to resolve canvas sizing issues
            if (tabId === 'dashboard') {
                updateDashboardCharts();
            }
        });
    });
}

function updateUILabelsAndQuantities(oldSystem, newSystem) {
    const keys = [
        "wall_brick", "wall_aac", "wall_concrete",
        "insulation_25", "insulation_50", "insulation_75", "insulation_100",
        "glazing_single", "glazing_double", "glazing_triple"
    ];

    keys.forEach(k => {
        const inputEl = document.getElementById(`qty-${k}`);
        if (!inputEl) return;

        let val = parseFloat(inputEl.value) || 0;
        const isArea = k.includes("insulation") || k.includes("glazing");

        // Convert input value
        if (oldSystem === 'metric' && newSystem === 'imperial') {
            val = val * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
        } else if (oldSystem === 'imperial' && newSystem === 'metric') {
            val = val / (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
        }
        inputEl.value = val.toFixed(2);

        // Update label
        const label = document.querySelector(`label[for="qty-${k}"]`);
        if (label) {
            let labelText = label.innerText;
            if (newSystem === 'imperial') {
                labelText = labelText.replace("(m²)", "(ft²)").replace("(m³)", "(ft³)");
            } else {
                labelText = labelText.replace("(ft²)", "(m²)").replace("(ft³)", "(m³)");
            }
            label.innerText = labelText;
        }
    });
}

// -------------------------------------------------------------
// DATABASE FETCH & DISPLAY
// -------------------------------------------------------------
async function fetchDatabase() {
    try {
        const response = await fetch("/api/database");
        materialsDB = await response.json();
        renderDatabaseEditor();
    } catch (e) {
        console.error("Failed to load database rates:", e);
    }
}

function renderDatabaseEditor() {
    const tbody = document.querySelector("#table-database tbody");
    tbody.innerHTML = "";

    for (const [key, mat] of Object.entries(materialsDB)) {
        let displayUnit = mat.unit;
        let displayCarbon = mat.carbon;
        let displayCost = mat.cost;

        if (currentUnitSystem === 'imperial') {
            const isArea = mat.unit.includes("²");
            if (isArea) {
                displayUnit = "ft²";
                displayCarbon = mat.carbon / CONV_SQM_TO_SQFT;
                displayCost = mat.cost / CONV_SQM_TO_SQFT;
            } else if (mat.unit.includes("³")) {
                displayUnit = "ft³";
                displayCarbon = mat.carbon / CONV_CUM_TO_CUFT;
                displayCost = mat.cost / CONV_CUM_TO_CUFT;
            }
        }

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><strong>${key}</strong></td>
            <td>${mat.name}</td>
            <td>${displayUnit}</td>
            <td><input type="number" step="0.0001" class="db-carbon-input" data-key="${key}" value="${displayCarbon.toFixed(4)}"></td>
            <td><input type="number" step="0.01" class="db-cost-input" data-key="${key}" value="${displayCost.toFixed(2)}"></td>
        `;
        tbody.appendChild(tr);
    }

    // Save and Reset hooks
    document.getElementById("btn-save-db").onclick = () => {
        const carbonInputs = document.querySelectorAll(".db-carbon-input");
        const costInputs = document.querySelectorAll(".db-cost-input");

        carbonInputs.forEach(input => {
            const key = input.getAttribute("data-key");
            let val = parseFloat(input.value) || 0;
            if (currentUnitSystem === 'imperial') {
                const isArea = materialsDB[key].unit.includes("²");
                val = val * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
            }
            materialsDB[key].carbon = val;
        });

        costInputs.forEach(input => {
            const key = input.getAttribute("data-key");
            let val = parseFloat(input.value) || 0;
            if (currentUnitSystem === 'imperial') {
                const isArea = materialsDB[key].unit.includes("²");
                val = val * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
            }
            materialsDB[key].cost = val;
        });

        alert("Rates database updated successfully! Calculations will now use these updated rates.");
        recalculateBIMEstimates();
    };

    document.getElementById("btn-reset-db").onclick = async () => {
        if (confirm("Reset database to standard Pakistan defaults?")) {
            await fetchDatabase();
        }
    };
}

// -------------------------------------------------------------
// DASHBOARD INPUTS CONTROLS
// -------------------------------------------------------------
function setupDashboardControls() {
    const wallU = document.getElementById("input-wall-u");
    const roofU = document.getElementById("input-roof-u");
    const windowU = document.getElementById("input-window-u");
    const gfaInput = document.getElementById("input-gfa");
    const lifespanInput = document.getElementById("input-lifespan");

    const updateML = async () => {
        // Update Labels
        document.getElementById("lbl-wall-u").innerText = parseFloat(wallU.value).toFixed(4);
        document.getElementById("lbl-roof-u").innerText = parseFloat(roofU.value).toFixed(4);
        document.getElementById("lbl-window-u").innerText = parseFloat(windowU.value).toFixed(4);
        document.getElementById("header-gfa-val").innerText = parseFloat(gfaInput.value).toLocaleString();

        // Call Predict API
        try {
            const response = await fetch("/api/predict", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    wall_u: parseFloat(wallU.value),
                    roof_u: parseFloat(roofU.value),
                    window_u: parseFloat(windowU.value),
                    gfa: parseFloat(gfaInput.value)
                })
            });
            const data = await response.json();
            if (data.success) {
                predictedMetrics = data.predictions;
                updateDashboardUI();
            }
        } catch (e) {
            console.error("ML prediction failed:", e);
        }
    };

    // Listeners
    wallU.addEventListener("input", updateML);
    roofU.addEventListener("input", updateML);
    windowU.addEventListener("input", updateML);
    gfaInput.addEventListener("change", updateML);
    lifespanInput.addEventListener("change", () => {
        updateDashboardUI(); // Recalculate LCA carbon chart & totals when lifespan years change
    });

    // Run once initially
    updateML();
    window.updateML = updateML;

    // BIM Quantities Recalculate button
    document.getElementById("btn-recalculate-qty").onclick = () => {
        recalculateBIMEstimates();
        alert("BIM quantities synchronized with dashboard!");
    };
}

// -------------------------------------------------------------
// CALCULATOR & RECALCULATE BIM VALUES
// -------------------------------------------------------------
async function recalculateBIMEstimates() {
    // Read current quantities from forms
    const keys = [
        "wall_brick", "wall_aac", "wall_concrete",
        "insulation_25", "insulation_50", "insulation_75", "insulation_100",
        "glazing_single", "glazing_double", "glazing_triple"
    ];
    
    currentQuantities = {};
    keys.forEach(k => {
        let val = parseFloat(document.getElementById(`qty-${k}`).value) || 0;
        if (currentUnitSystem === 'imperial') {
            const isArea = k.includes("insulation") || k.includes("glazing");
            val = val / (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
        }
        currentQuantities[k] = val;
    });

    try {
        const response = await fetch("/api/calculate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                quantities: currentQuantities,
                database: materialsDB
            })
        });
        const data = await response.json();
        
        if (data.success) {
            // Update Summary Totals
            document.getElementById("val-embodied-carbon").innerText = data.totals.total_embodied_carbon.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            document.getElementById("val-material-cost").innerText = data.totals.total_cost.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            
            // Save totals in state
            currentQuantities.total_embodied_carbon = data.totals.total_embodied_carbon;
            currentQuantities.total_cost = data.totals.total_cost;
            currentQuantities.breakdown = data.materials;

            // Render Detailed Breakdown Table
            renderBreakdownTable(data.materials);
            
            // Sync graphs
            updateDashboardUI();
        }
    } catch (e) {
        console.error("Cost/Embodied Carbon calculation failed:", e);
    }
}

function renderBreakdownTable(materials) {
    const tbody = document.querySelector("#table-breakdown tbody");
    tbody.innerHTML = "";

    if (Object.keys(materials).length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center;">No quantities entered. Populate fields above or upload a Revit CSV.</td></tr>`;
        return;
    }

    for (const [key, item] of Object.entries(materials)) {
        let displayQty = item.quantity;
        let displayUnit = item.unit;
        let displayUnitCarbon = item.unit_carbon;
        let displayUnitCost = item.unit_cost;

        if (currentUnitSystem === 'imperial') {
            const isArea = item.unit.includes("²");
            if (isArea) {
                displayQty = item.quantity * CONV_SQM_TO_SQFT;
                displayUnit = "ft²";
                displayUnitCarbon = item.unit_carbon / CONV_SQM_TO_SQFT;
                displayUnitCost = item.unit_cost / CONV_SQM_TO_SQFT;
            } else if (item.unit.includes("³")) {
                displayQty = item.quantity * CONV_CUM_TO_CUFT;
                displayUnit = "ft³";
                displayUnitCarbon = item.unit_carbon / CONV_CUM_TO_CUFT;
                displayUnitCost = item.unit_cost / CONV_CUM_TO_CUFT;
            }
        }

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><strong>${item.name}</strong></td>
            <td>${displayQty.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td>${displayUnit}</td>
            <td>${displayUnitCarbon.toFixed(4)}</td>
            <td>${item.total_carbon.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td>${displayUnitCost.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td>${item.total_cost.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
        `;
        tbody.appendChild(tr);
    }
}

// -------------------------------------------------------------
// METRIC UI UPDATES & LIFECYCLE LCA CALCULATIONS
// -------------------------------------------------------------
function updateDashboardUI() {
    if (!predictedMetrics.operational_carbon || currentQuantities.total_embodied_carbon === undefined) return;

    const opEnergy = predictedMetrics.operational_energy_kbtu;
    const eui = predictedMetrics.eui;
    const opCarbon = predictedMetrics.operational_carbon; // kgCO2e/year
    const embodiedCarbon = currentQuantities.total_embodied_carbon; // kgCO2e
    const lifespan = parseFloat(document.getElementById("input-lifespan").value) || 50;

    // Update operational carbon card
    document.getElementById("val-op-energy").innerText = opEnergy.toLocaleString(undefined, {maximumFractionDigits: 2});
    document.getElementById("val-eui").innerText = eui.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    document.getElementById("val-op-carbon").innerText = opCarbon.toLocaleString(undefined, {maximumFractionDigits: 2});

    // LCA calculation: total carbon over analysis lifespan
    // Total Carbon (Tonnes) = (Embodied + Lifespan * Operational) / 1000
    const totalLcaCarbonKg = embodiedCarbon + (opCarbon * lifespan);
    const totalLcaCarbonTonnes = totalLcaCarbonKg / 1000;
    
    document.getElementById("val-lca-total").innerText = totalLcaCarbonTonnes.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1});

    // Breakdown Percentages
    const embodiedPercent = totalLcaCarbonKg > 0 ? Math.round((embodiedCarbon / totalLcaCarbonKg) * 100) : 0;
    const opPercent = totalLcaCarbonKg > 0 ? 100 - embodiedPercent : 0;

    document.getElementById("lbl-lca-embodied").innerText = `${embodiedPercent}%`;
    document.getElementById("lbl-lca-operational").innerText = `${opPercent}%`;

    // Re-draw Charts
    updateDashboardCharts();
}

// -------------------------------------------------------------
// VISUALIZATIONS (CHART.JS)
// -------------------------------------------------------------
function updateDashboardCharts() {
    // 1. Cost breakdown Chart
    const costCtx = document.getElementById("costChart");
    if (!costCtx) return;

    const labels = [];
    const costs = [];
    const colors = ['#3b82f6', '#10b981', '#f97316', '#8b5cf6', '#ef4444', '#eab308', '#6366f1'];

    if (currentQuantities.breakdown) {
        Object.values(currentQuantities.breakdown).forEach(item => {
            labels.push(item.name);
            costs.push(item.total_cost);
        });
    }

    if (costChart) costChart.destroy();
    
    if (costs.length === 0) {
        costChart = null;
        costCtx.getContext('2d').clearRect(0,0,300,300);
    } else {
        costChart = new Chart(costCtx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: costs,
                    backgroundColor: colors.slice(0, costs.length),
                    borderWidth: 1,
                    borderColor: 'rgba(255, 255, 255, 0.1)'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { color: '#f3f4f6', font: { family: 'Inter', size: 10 } }
                    }
                }
            }
        });
    }

    // 2. Cumulative Life Cycle Carbon Chart
    const carbonCtx = document.getElementById("carbonChart");
    if (!carbonCtx) return;

    const lifespan = parseFloat(document.getElementById("input-lifespan").value) || 50;
    const embodiedCarbon = currentQuantities.total_embodied_carbon || 0;
    const opCarbon = predictedMetrics.operational_carbon || 0;

    const chartYears = [];
    const embodiedSeries = [];
    const operationalSeries = [];
    const totalSeries = [];

    // Sample 5 points over the lifespan for simple plotting
    const intervals = 5;
    for (let i = 0; i <= intervals; i++) {
        const year = Math.round((lifespan / intervals) * i);
        chartYears.push(`Yr ${year}`);
        
        const cumulativeOp = opCarbon * year;
        
        embodiedSeries.push(embodiedCarbon / 1000); // tonnes
        operationalSeries.push(cumulativeOp / 1000); // tonnes
        totalSeries.push((embodiedCarbon + cumulativeOp) / 1000); // tonnes
    }

    if (carbonChart) carbonChart.destroy();

    carbonChart = new Chart(carbonCtx, {
        type: 'line',
        data: {
            labels: chartYears,
            datasets: [
                {
                    label: 'Cumulative Operational CO₂',
                    data: operationalSeries,
                    backgroundColor: 'rgba(249, 115, 22, 0.15)',
                    borderColor: '#f97316',
                    fill: true,
                    tension: 0.3
                },
                {
                    label: 'Initial Embodied CO₂',
                    data: embodiedSeries,
                    borderColor: '#3b82f6',
                    borderDash: [5, 5],
                    fill: false,
                    tension: 0
                },
                {
                    label: 'Total LCA Carbon Footprint',
                    data: totalSeries,
                    borderColor: '#14b8a6',
                    borderWidth: 3,
                    fill: false,
                    tension: 0.1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    title: { display: true, text: 'CO₂ Emissions (Tonnes)', color: '#f3f4f6' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af' }
                },
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af' }
                }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#f3f4f6', font: { family: 'Inter', size: 10 } }
                }
            }
        }
    });
}

// -------------------------------------------------------------
// DESIGN OPTIONS / ALTERNATIVES COMPARATIVE ANALYSIS
// -------------------------------------------------------------
async function runAlternativesAnalysis() {
    // 1. Calculate base quantities from BIM calculator inputs to preserve volume scaling
    const wallVol = (parseFloat(document.getElementById("qty-wall_brick").value) || 0) + 
                    (parseFloat(document.getElementById("qty-wall_aac").value) || 0) + 
                    (parseFloat(document.getElementById("qty-wall_concrete").value) || 0);

    const insulationArea = (parseFloat(document.getElementById("qty-insulation_25").value) || 0) + 
                           (parseFloat(document.getElementById("qty-insulation_50").value) || 0) + 
                           (parseFloat(document.getElementById("qty-insulation_75").value) || 0) + 
                           (parseFloat(document.getElementById("qty-insulation_100").value) || 0);

    const windowArea = (parseFloat(document.getElementById("qty-glazing_single").value) || 0) + 
                       (parseFloat(document.getElementById("qty-glazing_double").value) || 0) + 
                       (parseFloat(document.getElementById("qty-glazing_triple").value) || 0);

    const gfa = parseFloat(document.getElementById("input-gfa").value) || 13447.45;
    const lifespan = parseFloat(document.getElementById("input-lifespan").value) || 50;

    const alternativesData = [];

    // Helper to evaluate each alternative option
    for (let i = 1; i <= 3; i++) {
        const wallMat = document.getElementById(`alt${i}-wall`).value;
        const insType = document.getElementById(`alt${i}-insulation`).value;
        const glazeType = document.getElementById(`alt${i}-glazing`).value;

        // Embodied Cost & Carbon Calculations
        const wallCost = wallVol * (materialsDB[wallMat]?.cost || 0);
        const wallCarbon = wallVol * (materialsDB[wallMat]?.carbon || 0);

        const insCost = insType !== "none" ? insulationArea * (materialsDB[insType]?.cost || 0) : 0;
        const insCarbon = insType !== "none" ? insulationArea * (materialsDB[insType]?.carbon || 0) : 0;

        const glazeCost = windowArea * (materialsDB[glazeType]?.cost || 0);
        const glazeCarbon = windowArea * (materialsDB[glazeType]?.carbon || 0);

        const totalCost = wallCost + insCost + glazeCost;
        const totalEmbodiedCarbon = wallCarbon + insCarbon + glazeCarbon;

        // Map to U-values
        const [wall_u, roof_u] = WALL_INSULATION_U_MAPPING[wallMat][insType];
        const window_u = GLAZING_U_MAPPING[glazeType];

        // Call ML predictions from Backend
        let opCarbon = 0;
        try {
            const response = await fetch("/api/predict", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ wall_u, roof_u, window_u, gfa })
            });
            const data = await response.json();
            if (data.success) {
                opCarbon = data.predictions.operational_carbon;
            }
        } catch (e) {
            console.error(`ML prediction failed for Option ${i}:`, e);
        }

        const totalLcaCarbon = totalEmbodiedCarbon + (opCarbon * lifespan);

        alternativesData.push({
            option: `Option ${i}`,
            embodied: totalEmbodiedCarbon,
            operational: opCarbon * lifespan,
            totalLca: totalLcaCarbon,
            cost: totalCost
        });
    }

    // Render Alternative Charts
    renderAlternativesCharts(alternativesData);
}

function renderAlternativesCharts(data) {
    // 1. Cost Chart
    const costCtx = document.getElementById("altCostChart");
    if (altCostChart) altCostChart.destroy();

    altCostChart = new Chart(costCtx, {
        type: 'bar',
        data: {
            labels: ['Option 1 (Baseline)', 'Option 2', 'Option 3'],
            datasets: [{
                label: 'Initial Capital Material Cost (PKR)',
                data: [data[0].cost, data[1].cost, data[2].cost],
                backgroundColor: ['#3b82f6', '#f97316', '#14b8a6'],
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    title: { display: true, text: 'PKR', color: '#f3f4f6' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af' }
                },
                x: { ticks: { color: '#9ca3af' } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });

    // 2. Carbon Footprint Chart
    const carbonCtx = document.getElementById("altCarbonChart");
    if (altCarbonChart) altCarbonChart.destroy();

    altCarbonChart = new Chart(carbonCtx, {
        type: 'bar',
        data: {
            labels: ['Option 1 (Baseline)', 'Option 2', 'Option 3'],
            datasets: [
                {
                    label: 'Initial Embodied Carbon (kgCO₂e)',
                    data: [data[0].embodied, data[1].embodied, data[2].embodied],
                    backgroundColor: 'rgba(59, 130, 246, 0.75)'
                },
                {
                    label: 'Cumulative Operational Carbon (kgCO₂e)',
                    data: [data[0].operational, data[1].operational, data[2].operational],
                    backgroundColor: 'rgba(249, 115, 22, 0.75)'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    stacked: true,
                    title: { display: true, text: 'kgCO₂e Emissions', color: '#f3f4f6' },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af' }
                },
                x: {
                    stacked: true,
                    ticks: { color: '#9ca3af' }
                }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#f3f4f6' }
                }
            }
        }
    });
}

// -------------------------------------------------------------
// REVIT CSV TAKE-OFF PARSER
// -------------------------------------------------------------
function setupCSVUpload() {
    const dropzone = document.getElementById("csv-dropzone");
    const fileInput = document.getElementById("csv-file-input");
    const processBtn = document.getElementById("btn-process-csv");
    let selectedFile = null;

    // Click trigger
    dropzone.addEventListener("click", () => fileInput.click());

    // Drag-over styling
    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            handleFileSelection(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            handleFileSelection(fileInput.files[0]);
        }
    });

    function handleFileSelection(file) {
        selectedFile = file;
        dropzone.querySelector("p").innerHTML = `Selected File: <strong>${file.name}</strong> (${(file.size / 1024).toFixed(1)} KB)`;
        dropzone.querySelector("i").style.color = "#14b8a6";
        processBtn.removeAttribute("disabled");
    }

    processBtn.onclick = () => {
        if (!selectedFile) return;

        const reader = new FileReader();
        reader.onload = function (e) {
            const text = e.target.result;
            parseRevitCSV(text);
        };
        reader.readAsText(selectedFile);
    };
}

function parseRevitCSV(csvContent) {
    const lines = csvContent.split(/\r?\n/);
    const parsedData = [];
    
    // Mapped results accumulator
    const results = {
        wall_brick: 0,
        wall_aac: 0,
        wall_concrete: 0,
        insulation_25: 0,
        insulation_50: 0,
        insulation_75: 0,
        insulation_100: 0,
        glazing_single: 0,
        glazing_double: 0,
        glazing_triple: 0
    };

    lines.forEach((line, index) => {
        if (!line.trim() || index === 0) return; // skip header/empty lines

        const columns = line.split(/,|;/); // handle comma or semi-colon delimiters
        
        // Join all fields to search keywords
        const rowText = line.toLowerCase();
        
        // Find numerical values in row
        const numbers = [];
        columns.forEach(col => {
            const cleaned = col.replace(/[^\d\.]/g, "").trim();
            if (cleaned && !isNaN(cleaned)) {
                numbers.push(parseFloat(cleaned));
            }
        });

        // Skip if no numbers found in row
        if (numbers.length === 0) return;

        // Revit take-offs: Volume is typically in m3, Area in m2.
        // We look for largest numbers in columns or assume the last/middle numeric columns represent qty
        let qty = numbers[0];
        // If there are multiple numbers, find one that looks like a quantity
        if (numbers.length > 1) {
            qty = numbers[numbers.length - 1]; // typically the last column is Total/Quantity
        }

        let mappedKey = "";
        let category = "";
        let unit = "";

        // Keyword checking logic
        if (rowText.includes("brick")) {
            mappedKey = "wall_brick";
            category = "Brick Wall Block";
            unit = "m³";
            results.wall_brick += qty;
        } else if (rowText.includes("aac")) {
            mappedKey = "wall_aac";
            category = "AAC Block Wall";
            unit = "m³";
            results.wall_aac += qty;
        } else if (rowText.includes("concrete") && (rowText.includes("block") || rowText.includes("cmu") || rowText.includes("masonry"))) {
            mappedKey = "wall_concrete";
            category = "Concrete Block Wall";
            unit = "m³";
            results.wall_concrete += qty;
        } else if (rowText.includes("eps") || rowText.includes("insulation") || rowText.includes("polystyrene") || rowText.includes("eifs") || rowText.includes("efis")) {
            unit = "m²";
            if (rowText.includes("100") || rowText.includes("100mm")) {
                mappedKey = "insulation_100";
                category = "EPS Insulation 100mm";
                results.insulation_100 += qty;
            } else if (rowText.includes("75") || rowText.includes("75mm")) {
                mappedKey = "insulation_75";
                category = "EPS Insulation 75mm";
                results.insulation_75 += qty;
            } else if (rowText.includes("50") || rowText.includes("50mm")) {
                mappedKey = "insulation_50";
                category = "EPS Insulation 50mm";
                results.insulation_50 += qty;
            } else if (rowText.includes("25") || rowText.includes("25mm")) {
                mappedKey = "insulation_25";
                category = "EPS Insulation 25mm";
                results.insulation_25 += qty;
            } else {
                mappedKey = "insulation_50";
                category = "EPS Insulation 50mm (Default)";
                results.insulation_50 += qty;
            }
        } else if (rowText.includes("glazing") || rowText.includes("glass") || rowText.includes("window")) {
            unit = "m²";
            if (rowText.includes("triple")) {
                mappedKey = "glazing_triple";
                category = "Triple Glazing Window";
                results.glazing_triple += qty;
            } else if (rowText.includes("double")) {
                mappedKey = "glazing_double";
                category = "Double Glazing Window";
                results.glazing_double += qty;
            } else if (rowText.includes("single")) {
                mappedKey = "glazing_single";
                category = "Single Glazing Window";
                results.glazing_single += qty;
            }
        }

        if (mappedKey) {
            parsedData.push({ category, qty, unit, mappedKey });
        }
    });

    // Populate UI
    let mappedCount = 0;
    for (const [key, val] of Object.entries(results)) {
        if (val > 0) {
            let convertedVal = val;
            if (currentUnitSystem === 'imperial') {
                const isArea = key.includes("insulation") || key.includes("glazing");
                convertedVal = val * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
            }
            document.getElementById(`qty-${key}`).value = convertedVal.toFixed(2);
            mappedCount++;
        } else {
            document.getElementById(`qty-${key}`).value = 0;
        }
    }

    if (mappedCount > 0) {
        // Display summary table
        const card = document.getElementById("parsed-results-card");
        const tbody = document.querySelector("#table-parsed-csv tbody");
        tbody.innerHTML = "";
        
        parsedData.forEach(item => {
            let displayQty = item.qty;
            let displayUnit = item.unit;
            if (currentUnitSystem === 'imperial') {
                const isArea = item.unit.includes("²");
                displayQty = item.qty * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
                displayUnit = isArea ? "ft²" : "ft³";
            }
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${item.category}</strong></td>
                <td>${displayQty.toFixed(2)}</td>
                <td>${displayUnit}</td>
                <td><span class="info-label">${item.mappedKey}</span></td>
            `;
            tbody.appendChild(tr);
        });

        card.style.display = "block";
        alert(`CSV parse complete! Successfully mapped ${mappedCount} material categories from Revit. BIM quantities fields have been updated. Click 'Recalculate & Sync Dashboard' under the BIM Quantities tab to update estimates.`);
        
        // Go to calculator tab to review
        document.querySelector(".nav-item[data-tab='calculator']").click();
    } else {
        alert("We read the CSV file but could not match any materials to our scope (brick/aac/concrete block, EPS, single/double/triple glazing). Make sure your CSV contains materials takeoff data with these names.");
    }
}

// -------------------------------------------------------------
// LIVE SYNC PULL FROM REVIT (DYNAMO / C# / PYREVIT API)
// -------------------------------------------------------------
async function pullRevitLiveSync() {
    try {
        const response = await fetch("/api/revit-pull");
        const data = await response.json();
        if (data.success) {
            const quantities = data.quantities;
            const uValues = data.u_values;
            const gfa = data.gfa;

            let updatedItems = [];

            // 1. Process quantities
            let quantitiesUpdated = false;
            if (quantities && Object.keys(quantities).length > 0) {
                let mappedCount = 0;
                const parsedData = [];
                
                for (const [key, val] of Object.entries(quantities)) {
                    const inputEl = document.getElementById(`qty-${key}`);
                    if (inputEl) {
                        const parsedVal = parseFloat(val) || 0;
                        let convertedVal = parsedVal;
                        if (currentUnitSystem === 'imperial') {
                            const isArea = key.includes("insulation") || key.includes("glazing");
                            convertedVal = parsedVal * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
                        }
                        inputEl.value = convertedVal.toFixed(2);
                        if (parsedVal > 0) {
                            mappedCount++;
                            // Lookup material display name
                            const displayName = materialsDB[key] ? materialsDB[key].name : key;
                            let displayUnit = materialsDB[key] ? materialsDB[key].unit : "";
                            let displayQty = parsedVal;
                            if (currentUnitSystem === 'imperial') {
                                const isArea = displayUnit.includes("²");
                                displayQty = parsedVal * (isArea ? CONV_SQM_TO_SQFT : CONV_CUM_TO_CUFT);
                                displayUnit = isArea ? "ft²" : "ft³";
                            }
                            parsedData.push({
                                category: displayName,
                                qty: displayQty,
                                unit: displayUnit,
                                mappedKey: key
                            });
                        }
                    }
                }
                
                if (mappedCount > 0) {
                    quantitiesUpdated = true;
                    const card = document.getElementById("parsed-results-card");
                    const tbody = document.querySelector("#table-parsed-csv tbody");
                    tbody.innerHTML = "";
                    
                    parsedData.forEach(item => {
                        const tr = document.createElement("tr");
                        tr.innerHTML = `
                            <td><strong>${item.category}</strong></td>
                            <td>${item.qty.toFixed(2)}</td>
                            <td>${item.unit}</td>
                            <td><span class="info-label">${item.mappedKey}</span></td>
                        `;
                        tbody.appendChild(tr);
                    });
                    
                    card.style.display = "block";
                    updatedItems.push(`${mappedCount} material categories`);
                }
            }

            // 2. Process U-values
            let uValuesUpdated = false;
            if (uValues && Object.keys(uValues).length > 0) {
                const wallU = document.getElementById("input-wall-u");
                const roofU = document.getElementById("input-roof-u");
                const windowU = document.getElementById("input-window-u");

                if (uValues.wall_u !== undefined && uValues.wall_u !== null) {
                    wallU.value = uValues.wall_u;
                    document.getElementById("lbl-wall-u").innerText = parseFloat(uValues.wall_u).toFixed(4);
                    uValuesUpdated = true;
                }
                if (uValues.roof_u !== undefined && uValues.roof_u !== null) {
                    roofU.value = uValues.roof_u;
                    document.getElementById("lbl-roof-u").innerText = parseFloat(uValues.roof_u).toFixed(4);
                    uValuesUpdated = true;
                }
                if (uValues.window_u !== undefined && uValues.window_u !== null) {
                    windowU.value = uValues.window_u;
                    document.getElementById("lbl-window-u").innerText = parseFloat(uValues.window_u).toFixed(4);
                    uValuesUpdated = true;
                }
                if (uValuesUpdated) {
                    updatedItems.push("U-values");
                }
            }

            // 3. Process GFA
            if (gfa !== undefined && gfa !== null) {
                const gfaInput = document.getElementById("input-gfa");
                gfaInput.value = gfa;
                document.getElementById("header-gfa-val").innerText = parseFloat(gfa).toLocaleString();
                uValuesUpdated = true;
                updatedItems.push("Gross Floor Area");
            }

            if (updatedItems.length > 0) {
                // If quantities were updated, recalculate estimates
                if (quantitiesUpdated) {
                    await recalculateBIMEstimates();
                }
                
                // If U-values or GFA were updated, trigger ML predictions
                if (uValuesUpdated && typeof window.updateML === "function") {
                    await window.updateML();
                }

                alert(`Live API Sync complete! Successfully synchronized: ${updatedItems.join(", ")}. Review them in the Dashboard & Calculator.`);
                
                // Switch tab dynamically
                if (quantitiesUpdated && !uValuesUpdated) {
                    document.querySelector(".nav-item[data-tab='calculator']").click();
                } else {
                    document.querySelector(".nav-item[data-tab='dashboard']").click();
                }
            } else {
                alert("Live sync retrieved data, but no quantities, U-values, or GFA were set/updated.");
            }
        }
    } catch (e) {
        console.error("Failed to pull live sync data:", e);
        alert("Failed to connect to direct sync API. Check if your Flask server is running on port 5000.");
    }
}
