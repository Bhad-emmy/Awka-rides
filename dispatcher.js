const API_BASE = document.querySelector('meta[name="awka-api-base"]')?.content || "/api";
const state = { rides: [], selectedRide: null, drivers: [], selectedDriverId: null, map: null, markers: [], mutationInProgress: false };
const $ = (selector) => document.querySelector(selector);
const statusClass = (status) => String(status || "").replaceAll(" ", "-");
const display = (value) => value === null || value === undefined || value === "" ? "Not provided" : String(value);
const html = (value) => display(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[character]));
const money = (value) => value === null || value === undefined || value === "" ? "Not confirmed" : `₦${Number(value).toLocaleString()}`;


const ALERT_POLL_MS = 5000;
const knownRequestedRideIds = new Set();
let alertPollTimer = null;
let latestNewRide = null;
let audioContext = null;
let alertsInitialized = false;

function updateAlertsButton() {
    const button = $("#alerts-button");
    if (!button) return;
    if (!("Notification" in window)) {
        button.textContent = "🔔 Alerts unavailable";
        button.disabled = true;
        return;
    }
    if (Notification.permission === "granted") {
        button.textContent = "🔔 Alerts enabled";
    } else if (Notification.permission === "denied") {
        button.textContent = "🔕 Alerts blocked";
    } else {
        button.textContent = "🔔 Enable alerts";
    }
}

async function enableAlerts() {
    if (!("Notification" in window)) {
        setFeedback("This browser does not support desktop notifications.");
        return;
    }
    try {
        const permission = await Notification.requestPermission();
        updateAlertsButton();
        if (permission === "granted") {
            unlockAlertSound();
            setFeedback("Ride alerts enabled. Keep the dispatcher tab open.", true);
        } else if (permission === "denied") {
            setFeedback("Browser notifications are blocked. The dispatcher will still show new-ride alerts on the page.");
        }
    } catch (error) {
        setFeedback("Unable to enable browser notifications.");
    }
}

function unlockAlertSound() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        audioContext ||= new AudioContext();
        if (audioContext.state === "suspended") audioContext.resume();
    } catch (_) {}
}

function playRideAlertSound() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) return;
        audioContext ||= new AudioContext();
        if (audioContext.state === "suspended") audioContext.resume();
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.18, audioContext.currentTime + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.35);
        oscillator.connect(gain);
        gain.connect(audioContext.destination);
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 0.36);
    } catch (_) {}
    try {
        if ("vibrate" in navigator) navigator.vibrate([180, 90, 180]);
    } catch (_) {}
}

function showNewRideAlert(ride) {
    latestNewRide = ride;
    $("#new-ride-alert-title").textContent = `New ride request · ${display(ride.reference)}`;
    $("#new-ride-alert-body").textContent = `${display(ride.name)} · ${display(ride.pickup)} → ${display(ride.destination)}`;
    $("#new-ride-alert").hidden = false;
    playRideAlertSound();

    if ("Notification" in window && Notification.permission === "granted") {
        const notification = new Notification("Ọka-Rides · New ride", {
            body: `${display(ride.name)} · ${display(ride.pickup)} → ${display(ride.destination)}`,
            tag: `oka-rides-${ride.id}`,
            requireInteraction: true
        });
        notification.onclick = () => {
            window.focus();
            selectRide(ride.id);
            notification.close();
        };
    }
}

async function checkForNewRideRequests() {
    try {
        const data = await apiRequest("/rides?status=REQUESTED");
        const requestedRides = data.rides || [];
        const currentIds = new Set(requestedRides.map((ride) => ride.id));

        if (!alertsInitialized) {
            requestedRides.forEach((ride) => knownRequestedRideIds.add(ride.id));
            alertsInitialized = true;
            return;
        }

        const newRides = requestedRides.filter((ride) => !knownRequestedRideIds.has(ride.id));
        requestedRides.forEach((ride) => knownRequestedRideIds.add(ride.id));
        for (const id of [...knownRequestedRideIds]) {
            if (!currentIds.has(id)) knownRequestedRideIds.delete(id);
        }

        if (!newRides.length) return;

        const newestRide = newRides[0];
        showNewRideAlert(newestRide);
        await loadRides();

        if (state.selectedRide?.id === newestRide.id) {
            await selectRide(newestRide.id);
        }
    } catch (error) {
        if (!error.message.toLowerCase().includes("expired")) {
            console.warn("Ride alert check failed:", error);
        }
    }
}

function startRideAlerts() {
    clearInterval(alertPollTimer);
    alertPollTimer = setInterval(checkForNewRideRequests, ALERT_POLL_MS);
}

function showLogin(message = "") {
    $("#dispatcher-app").hidden = true;
    $("#login-panel").hidden = false;
    $("#logout-button").hidden = true;
    $("#login-feedback").textContent = message;
    $("#login-password").value = "";
}

function showDispatcher() {
    $("#login-panel").hidden = true;
    $("#dispatcher-app").hidden = false;
    $("#logout-button").hidden = false;
}

function setFeedback(message, success = false) {
    const element = $("#feedback");
    element.textContent = message;
    element.classList.toggle("success", success);
}

async function apiRequest(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        credentials: "include",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && !path.startsWith("/auth/")) {
        showLogin("Your dispatcher session has expired. Please log in again.");
    }
    if (!response.ok) throw new Error(body.detail || "The API request failed.");
    return body;
}

function setBusy(button, busy) {
    button.disabled = busy;
    button.dataset.originalText ||= button.textContent;
    button.textContent = busy ? "Working..." : button.dataset.originalText;
}

async function loadRides() {
    const filter = $("#status-filter").value;
    $("#ride-list").innerHTML = '<div class="empty">Loading rides...</div>';
    try {
        const data = await apiRequest(`/rides${filter ? `?status=${encodeURIComponent(filter)}` : ""}`);
        state.rides = data.rides || [];
        $("#ride-count").textContent = state.rides.length;
        renderRideList();
    } catch (error) {
        $("#ride-list").innerHTML = `<div class="empty">${html(error.message)}</div>`;
        setFeedback(error.message);
    }
}

function renderRideList() {
    const list = $("#ride-list");
    if (!state.rides.length) { list.innerHTML = '<div class="empty">No rides found.</div>'; return; }
    list.innerHTML = state.rides.map((ride) => `<button class="ride-item ${state.selectedRide?.id === ride.id ? "selected" : ""}" data-ride-id="${html(ride.id)}">
        <strong>${html(ride.reference)}</strong><span>${html(ride.name)} · ${html(ride.phone)}</span><span>${html(ride.pickup)} → ${html(ride.destination)}</span>
        <span class="ride-meta"><span>${html(ride.vehicle)}</span><span class="status ${html(statusClass(ride.status))}">${html(ride.status)}</span></span><span>${html(formatDate(ride.createdAt))}</span>
    </button>`).join("");
    list.querySelectorAll("[data-ride-id]").forEach((button) => button.addEventListener("click", () => selectRide(button.dataset.rideId)));
}

function formatDate(value) {
    if (!value) return "Created time unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? display(value) : date.toLocaleString();
}

async function selectRide(rideId) {
    try {
        const ride = await apiRequest(`/rides/${encodeURIComponent(rideId)}`);
        state.selectedRide = ride;
        state.selectedDriverId = null;
        renderRideList();
        renderDetails();
        await loadDrivers();
    } catch (error) { setFeedback(error.message); }
}

function detail(label, value, wide = false) {
    return `<div class="detail ${wide ? "wide" : ""}"><label>${html(label)}</label><span>${html(value)}</span></div>`;
}

function renderDetails() {
    const ride = state.selectedRide;
    $("#empty-details").hidden = Boolean(ride);
    $("#ride-details").hidden = !ride;
    if (!ride) return;
    $("#details-title").textContent = display(ride.reference);
    $("#details-subtitle").textContent = `${display(ride.name)} · ${display(ride.phone)}`;
    const status = $("#detail-status");
    status.textContent = display(ride.status); status.className = `status ${statusClass(ride.status)}`;
    $("#detail-grid").innerHTML = [
        detail("Customer", ride.name), detail("Phone", ride.phone), detail("Vehicle", ride.vehicle),
        detail("Pickup", ride.pickup), detail("Destination", ride.destination), detail("Pickup timing", ride.pickupTiming),
        detail("Scheduled time", ride.scheduledTime), detail("Passengers", ride.passengers), detail("Created", formatDate(ride.createdAt)),
        detail("Notes", ride.notes, true), detail("Driver ID", ride.driverId), detail("Driver name", ride.driverName),
        detail("Driver phone", ride.driverPhone), detail("Driver vehicle", ride.driverVehicle), detail("Driver plate", ride.driverPlateNumber),
        detail("Fare", money(ride.fare)), detail("Fare confirmed", ride.fareConfirmed ? "Yes" : "No"),
        detail("Payment status", ride.paymentStatus), detail("Payment reference", ride.paymentReference), detail("Payment method", ride.paymentMethod)
    ].join("");
    $("#address-summary").textContent = `${display(ride.pickup)} → ${display(ride.destination)}`;
    $("#fare-input").value = ride.fare ?? "";
    $("#fare-state").textContent = ride.fareConfirmed ? `Confirmed: ${money(ride.fare)}` : "Fare not confirmed.";
    $("#payment-state").textContent = `Status: ${display(ride.paymentStatus)} · Reference: ${display(ride.paymentReference)} · Method: ${display(ride.paymentMethod)}`;
    const canAssign = ride.status === "REQUESTED";
    const canConfirmFare = ride.status === "DRIVER ASSIGNED" && !ride.fareConfirmed;
    const canRecordPayment = ride.status === "DRIVER ASSIGNED" && ride.fareConfirmed;
    $("#assign-button").disabled = !canAssign;
    $("#fare-input").disabled = !canConfirmFare;
    $("#fare-button").disabled = !canConfirmFare;
    $("#payment-status").disabled = !canRecordPayment;
    $("#payment-reference").disabled = !canRecordPayment;
    $("#payment-method").disabled = !canRecordPayment;
    $("#payment-button").disabled = !canRecordPayment;
    renderLifecycleActions();
    renderMap();
}

function renderMap() {
    if (!state.map) state.map = L.map("ride-map").setView([6.2088, 7.0719], 13);
    state.markers.forEach((marker) => marker.remove()); state.markers = [];
    const ride = state.selectedRide; const points = [];
    if (Number.isFinite(Number(ride.pickupLatitude)) && Number.isFinite(Number(ride.pickupLongitude))) {
        const point = [Number(ride.pickupLatitude), Number(ride.pickupLongitude)]; state.markers.push(L.marker(point).addTo(state.map).bindPopup("Pickup")); points.push(point);
    }
    if (Number.isFinite(Number(ride.destinationLatitude)) && Number.isFinite(Number(ride.destinationLongitude))) {
        const point = [Number(ride.destinationLatitude), Number(ride.destinationLongitude)]; state.markers.push(L.marker(point).addTo(state.map).bindPopup("Destination")); points.push(point);
    }
    if (points.length) state.map.fitBounds(points, { padding: [25, 25], maxZoom: 15 });
    else state.map.setView([6.2088, 7.0719], 13);
    setTimeout(() => state.map.invalidateSize(), 0);
}

async function loadDrivers() {
    try {
        const data = await apiRequest("/drivers"); state.drivers = data.drivers || [];
        $("#driver-list").innerHTML = state.drivers.map((driver) => `<label class="driver-option ${state.selectedDriverId === driver.id ? "selected" : ""}">
            <input type="radio" name="driver" value="${html(driver.id)}" ${state.selectedDriverId === driver.id ? "checked" : ""}> <strong>${html(driver.name)}</strong>
            <small>${html(driver.vehicle)} · ${html(driver.plateNumber)}</small><small>${html(driver.completedRides || 0)} completed · ${html(driver.cancelledRides || 0)} cancelled · ${html(driver.status)}${driver.eligible ? " · Eligible" : ""}</small>
        </label>`).join("") || '<p class="muted">No drivers found.</p>';
        $("#driver-list").querySelectorAll("input").forEach((input) => input.addEventListener("change", (event) => { state.selectedDriverId = event.target.value; loadDrivers(); }));
    } catch (error) { $("#driver-list").innerHTML = `<p class="muted">${html(error.message)}</p>`; setFeedback(error.message); }
}

function renderLifecycleActions() {
    const actions = { REQUESTED: ["CANCELLED"], "DRIVER ASSIGNED": ["CANCELLED"], PAID: ["PICKED UP", "CANCELLED"], "PICKED UP": ["COMPLETED", "CANCELLED"] };
    $("#lifecycle-actions").innerHTML = (actions[state.selectedRide.status] || []).map((target) => `<button class="button ${target === "CANCELLED" ? "danger" : "secondary"}" data-target-status="${target}">${target}</button>`).join("") || '<span class="muted">No lifecycle actions available.</span>';
    $("#lifecycle-actions").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => updateStatus(button, button.dataset.targetStatus)));
}

async function mutate(button, path, body) {
    if (state.mutationInProgress) return;
    state.mutationInProgress = true;
    setBusy(button, true);
    try { await apiRequest(path, { method: "PATCH", body: JSON.stringify(body) }); setFeedback("Update saved.", true); await reloadSelected(); }
    catch (error) { setFeedback(error.message); } finally {
        setBusy(button, false);
        state.mutationInProgress = false;
        if (state.selectedRide) renderDetails();
    }
}

async function reloadSelected() {
    if (state.selectedRide) await selectRide(state.selectedRide.id);
    await loadRides();
}

async function assignDriver() {
    if (!state.selectedRide || !state.selectedDriverId) { setFeedback("Select an eligible driver first."); return; }
    if (state.mutationInProgress) return;
    state.mutationInProgress = true;
    const button = $("#assign-button"); setBusy(button, true);
    try { await apiRequest(`/rides/${encodeURIComponent(state.selectedRide.id)}/assign?driver_id=${encodeURIComponent(state.selectedDriverId)}`, { method: "POST" }); setFeedback("Driver assigned.", true); await reloadSelected(); }
    catch (error) { setFeedback(error.message); } finally {
        setBusy(button, false);
        state.mutationInProgress = false;
        if (state.selectedRide) renderDetails();
    }
}
async function confirmFare() {
    const value = Number($("#fare-input").value); if (!state.selectedRide || !Number.isFinite(value) || value <= 0) { setFeedback("Enter a fare greater than zero."); return; }
    await mutate($("#fare-button"), `/rides/${encodeURIComponent(state.selectedRide.id)}/fare`, { fare: value, fareConfirmed: true });
}
async function recordPayment() {
    if (!state.selectedRide) return;
    await mutate($("#payment-button"), `/rides/${encodeURIComponent(state.selectedRide.id)}/payment`, { paymentStatus: $("#payment-status").value, paymentReference: $("#payment-reference").value, paymentMethod: $("#payment-method").value });
}
async function updateStatus(button, status) { await mutate(button, `/rides/${encodeURIComponent(state.selectedRide.id)}/status`, { status }); }

$("#alerts-button").addEventListener("click", enableAlerts);
$("#view-new-ride-button").addEventListener("click", async () => {
    if (!latestNewRide) return;
    $("#new-ride-alert").hidden = true;
    await selectRide(latestNewRide.id);
});
$("#dismiss-new-ride-button").addEventListener("click", () => { $("#new-ride-alert").hidden = true; });
$("#refresh-button").addEventListener("click", loadRides);
$("#status-filter").addEventListener("change", loadRides);
$("#assign-button").addEventListener("click", assignDriver);
$("#fare-button").addEventListener("click", confirmFare);
$("#payment-button").addEventListener("click", recordPayment);

$("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#login-button");
    setBusy(button, true);
    $("#login-feedback").textContent = "";
    try {
        await apiRequest("/auth/login", {
            method: "POST",
            body: JSON.stringify({
                username: $("#login-username").value,
                password: $("#login-password").value
            })
        });
        showDispatcher();
        await loadRides();
        await checkForNewRideRequests();
        startRideAlerts();
    } catch (error) {
        $("#login-feedback").textContent = error.message;
    } finally {
        setBusy(button, false);
    }
});

$("#logout-button").addEventListener("click", async () => {
    const button = $("#logout-button");
    setBusy(button, true);
    try {
        await apiRequest("/auth/logout", { method: "POST" });
    } finally {
        state.selectedRide = null;
        state.rides = [];
        knownRequestedRideIds.clear();
        alertsInitialized = false;
        clearInterval(alertPollTimer);
        showLogin("You have been logged out.");
        setBusy(button, false);
    }
});

async function initialize() {
    updateAlertsButton();
    try {
        await apiRequest("/auth/session");
        showDispatcher();
        await loadRides();
        await checkForNewRideRequests();
        startRideAlerts();
    } catch (error) {
        showLogin();
        clearInterval(alertPollTimer);
    }
}

initialize();
