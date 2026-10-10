const API_BASE = document.querySelector('meta[name="awka-api-base"]')?.content || "/api";
const state = { rides: [], selectedRide: null, drivers: [], selectedDriverId: null, map: null, markers: [], mutationInProgress: false };
let csrfToken = null;
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
let rideAlertInterval = null;
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
        gain.gain.exponentialRampToValueAtTime(0.2, audioContext.currentTime + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.32);
        oscillator.connect(gain);
        gain.connect(audioContext.destination);
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 0.33);
    } catch (_) {}
    try {
        if ("vibrate" in navigator) navigator.vibrate([180, 90, 180]);
    } catch (_) {}
}

function stopRideAlert() {
    if (rideAlertInterval) {
        clearInterval(rideAlertInterval);
        rideAlertInterval = null;
    }
}

function startRideAlert() {
    stopRideAlert();
    playRideAlertSound();
    rideAlertInterval = setInterval(playRideAlertSound, 1200);
}

function showNewRideAlert(ride) {
    latestNewRide = ride;
    $("#new-ride-alert-title").textContent = `New ride request · ${display(ride.reference)}`;
    $("#new-ride-alert-body").textContent = `${display(ride.name)} · ${display(ride.pickup)} → ${display(ride.destination)}`;
    $("#new-ride-alert").hidden = false;
    startRideAlert();

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


// Keep the queue, selected ride state, driver choices, and payment history
// synchronized without a full-page reload. Do not rebuild editable controls
// while the dispatcher is using them.
let dashboardRefreshInFlight = false;
async function refreshDispatcherDashboard() {
    if (document.hidden || dashboardRefreshInFlight || $("#dispatcher-app").hidden || state.mutationInProgress) return;
    dashboardRefreshInFlight = true;
    try {
        await loadRides({ silent: true });
        const selectedId = state.selectedRide?.id;
        if (!selectedId || state.mutationInProgress) return;

        const active = document.activeElement;
        const detailControlsFocused = active && $("#ride-details").contains(active)
            && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName);
        if (detailControlsFocused) return;

        const updatedRide = await apiRequest(`/rides/${encodeURIComponent(selectedId)}`);
        if (state.selectedRide?.id !== selectedId) return;
        const statusChanged = state.selectedRide.status !== updatedRide.status;
        const driverChanged = state.selectedRide.driverId !== updatedRide.driverId;
        state.selectedRide = updatedRide;
        renderDetails();

        const work = [loadRidePaymentRecords(selectedId)];
        if (updatedRide.status === "REQUESTED" && (statusChanged || driverChanged || !state.selectedDriverId)) {
            work.push(loadDrivers());
        }
        await Promise.all(work);
    } catch (error) {
        if (!String(error.message || "").toLowerCase().includes("expired")) {
            console.warn("Dispatcher auto-refresh failed:", error);
        }
    } finally {
        dashboardRefreshInFlight = false;
    }
}
setInterval(refreshDispatcherDashboard, 5000);
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshDispatcherDashboard();
});

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
    const method = String(options.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
        headers["X-CSRF-Token"] = csrfToken;
    }
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        credentials: "include",
        headers
    });
    // Read the response body exactly once. A failed response.json() consumes
    // the stream, so calling response.text() in its catch hides the real error.
    const rawBody = await response.text().catch(() => "");
    let body = {};
    if (rawBody) {
        try {
            body = JSON.parse(rawBody);
        } catch (_) {
            body = { detail: rawBody.slice(0, 500) };
        }
    }
    if (response.status === 401 && !path.startsWith("/auth/")) {
        showLogin("Your dispatcher session has expired. Please log in again.");
    }
    if (!response.ok) {
        const detail = typeof body.detail === "string"
            ? body.detail
            : Array.isArray(body.detail)
                ? body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
                : body.message || body.error || "";
        const requestId = response.headers.get("x-vercel-id");
        const context = `${method} ${path} · HTTP ${response.status}`;
        console.error("Ọka-Rides API error", { context, requestId, detail: body });
        throw new Error(detail ? `${context}: ${detail}` : `${context}: API returned an unreadable error response`);
    }
    return body;
}

function setBusy(button, busy) {
    button.disabled = busy;
    button.dataset.originalText ||= button.textContent;
    button.textContent = busy ? "Working..." : button.dataset.originalText;
}

async function refreshGeneratedDriverId() {
    const input = $("#new-driver-id");
    if (!input) return;
    input.value = "Loading...";

    try {
        const data = await apiRequest("/drivers");
        const highestNumber = (data.drivers || []).reduce((highest, driver) => {
            const match = String(driver.driverId || "").match(/^oka(\d+)$/i);
            return match ? Math.max(highest, Number(match[1])) : highest;
        }, 0);
        input.value = `oka${String(highestNumber + 1).padStart(2, "0")}`;
    } catch (error) {
        input.value = "";
        setFeedback("Log in to generate the next driver ID.");
    }
}

$("#regenerate-driver-id").addEventListener("click", refreshGeneratedDriverId);

$("#create-driver-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = $("#create-driver-button");
    const driverId = $("#new-driver-id").value;
    const payload = {
        driverId,
        name: $("#new-driver-name").value.trim(),
        phone: $("#new-driver-phone").value.trim(),
        vehicle: $("#new-driver-vehicle").value.trim(),
        plateNumber: $("#new-driver-plate").value.trim().toUpperCase(),
        pin: $("#new-driver-pin").value,
        status: "ACTIVE",
        verificationStatus: "VERIFIED",
        notes: "Created by dispatcher; active and verified by automatic account policy."
    };

    if (!payload.name || !payload.phone || !payload.vehicle || !payload.plateNumber || payload.pin.length < 4) {
        setFeedback("Complete all driver fields. PIN must be at least 4 characters.");
        return;
    }

    setBusy(button, true);
    try {
        const result = await apiRequest("/drivers", {
            method: "POST",
            body: JSON.stringify(payload)
        });
        $("#new-driver-pin").value = "";
        $("#new-driver-name").value = "";
        $("#new-driver-phone").value = "";
        $("#new-driver-vehicle").value = "";
        $("#new-driver-plate").value = "";
        refreshGeneratedDriverId();
        setFeedback(`Driver created successfully. Driver ID: ${result.driverId || driverId}. Account is ACTIVE and VERIFIED.`, true);
        if (state.selectedRide) await loadDrivers();
    } catch (error) {
        setFeedback(error.message || "Could not create driver account. Check whether this generated ID already exists.");
    } finally {
        setBusy(button, false);
    }
});

async function loadRides({ silent = false } = {}) {
    const filter = $("#status-filter").value;
    if (!silent) $("#ride-list").innerHTML = '<div class="empty">Loading rides...</div>';
    try {
        const data = await apiRequest(`/rides${filter ? `?status=${encodeURIComponent(filter)}` : ""}`);
        state.rides = data.rides || [];

        // A selected ride can be deleted or become excluded by the current
        // status filter. Never leave its old details/actions on screen.
        if (state.selectedRide && !state.rides.some((ride) => ride.id === state.selectedRide.id)) {
            state.selectedRide = null;
            state.selectedDriverId = null;
            renderDetails();
        }

        // Clear an alert for a ride that no longer exists in the current queue.
        if (latestNewRide && !state.rides.some((ride) => ride.id === latestNewRide.id)) {
            latestNewRide = null;
            $("#new-ride-alert").hidden = true;
            stopRideAlert();
        }

        $("#ride-count").textContent = state.rides.length;
        renderRideList();
        setFeedback("");
    } catch (error) {
        if (!silent) {
            $("#ride-list").innerHTML = `<div class="empty">${html(error.message)}</div>`;
            setFeedback(error.message);
        } else {
            console.warn("Dispatcher ride refresh failed:", error);
        }
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

async function loadRidePaymentRecords(rideId) {
    const panel = $("#ride-payment-records");
    if (!panel) return;
    panel.textContent = "Loading payment records…";
    try {
        const data = await apiRequest(`/rides/${encodeURIComponent(rideId)}/payment-records`);
        const payments = data.payments || [];
        if (!payments.length) {
            panel.textContent = "No ledger entries for this ride yet.";
            return;
        }
        panel.innerHTML = '<h4>Payment history</h4>' + payments.map((payment) =>
            '<div class="detail" style="margin:6px 0"><strong>' + html(payment.status) + ' · ' + html(payment.method) +
            '</strong><br>Amount: ' + html(money(Number(payment.amount_kobo || 0) / 100)) +
            '<br>Reference: ' + html(payment.reference) +
            '<br>Recorded: ' + html(formatDate(payment.created_at)) +
            '<br>Paid at: ' + html(formatDate(payment.paid_at)) + '</div>'
        ).join("");
    } catch (error) {
        panel.textContent = "Unable to load payment history: " + error.message;
    }
}

async function selectRide(rideId) {
    try {
        const ride = await apiRequest(`/rides/${encodeURIComponent(rideId)}`);
        state.selectedRide = ride;
        state.selectedDriverId = null;
        renderRideList();
        renderDetails();
        await Promise.all([loadDrivers(), loadRidePaymentRecords(ride.id)]);
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
        detail("Payment status", ride.paymentStatus), detail("Payment method", ride.paymentMethod),
        detail("Payment reference", ride.paymentReference)
    ].join("");
    $("#address-summary").textContent = `${display(ride.pickup)} → ${display(ride.destination)}`;
    $("#fare-input").value = ride.fare ?? "";
    $("#fare-state").textContent = ride.fareConfirmed ? `Confirmed: ${money(ride.fare)}` : "Fare not confirmed.";
    $("#payment-state").textContent = `Status: ${display(ride.paymentStatus)} · Reference: ${display(ride.paymentReference)}`;
    const canAssign = ride.status === "REQUESTED";
    const canConfirmFare = ride.status === "DRIVER ASSIGNED" && !ride.fareConfirmed;
    const canRecordPayment = ride.status === "DRIVER ASSIGNED" && ride.fareConfirmed;
    $("#assign-button").disabled = !canAssign || !state.selectedDriverId;
    $("#fare-input").disabled = !canConfirmFare;
    $("#fare-button").disabled = !canConfirmFare;
    $("#payment-status").disabled = !canRecordPayment;
    $("#payment-reference").disabled = !canRecordPayment;
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
    if (!state.selectedRide) return;
    const list = $("#driver-list");
    const summary = $("#dispatch-summary");
    if (state.selectedRide.status !== "REQUESTED") {
        summary.textContent = "Dispatch is closed because this ride is no longer REQUESTED.";
        list.innerHTML = '<p class="muted">No dispatch options for this ride.</p>';
        $("#assign-button").disabled = true;
        return;
    }

    summary.textContent = "Loading available drivers and ranking them by dispatch eligibility and pickup distance...";
    list.innerHTML = '<p class="muted">Finding drivers...</p>';

    try {
        const data = await apiRequest(`/rides/${encodeURIComponent(state.selectedRide.id)}/dispatch-options`);
        state.drivers = data.drivers || [];
        const eligible = state.drivers.filter((driver) => driver.canAssign);
        summary.textContent = eligible.length
            ? `${eligible.length} driver${eligible.length === 1 ? "" : "s"} available. You choose who gets the ride.`
            : "No ONLINE eligible drivers are currently available.";

        list.innerHTML = state.drivers.map((driver) => {
            const available = Boolean(driver.canAssign);
            const distance = driver.distanceKm !== null && driver.distanceKm !== undefined
                ? `${driver.distanceKm} km away`
                : "Location unavailable";
            const badge = available
                ? '<span class="driver-badge">AVAILABLE</span>'
                : '<span class="driver-badge offline">NOT AVAILABLE</span>';
            return `<label class="driver-option ${state.selectedDriverId === driver.driverId ? "selected" : ""} ${available ? "" : "unavailable"}">
                <input type="radio" name="driver" value="${html(driver.driverId)}" ${state.selectedDriverId === driver.driverId ? "checked" : ""} ${available ? "" : "disabled"}>
                <strong>${html(driver.name)} <span class="driver-distance">${html(distance)}</span></strong>
                <small>${html(driver.vehicle)} · ${html(driver.plateNumber)} ${badge}</small>
                <small>${html(driver.completedRides || 0)} completed · ${html(driver.cancelledRides || 0)} cancelled · ${html(driver.availabilityStatus || "OFFLINE")}</small>
                <div class="driver-control">
                    <select class="driver-availability" data-driver-id="${html(driver.driverId)}" aria-label="Availability for ${html(driver.name)}">
                        ${["ONLINE","OFFLINE","BUSY"].map((status) => `<option value="${status}" ${String(driver.availabilityStatus || "OFFLINE") === status ? "selected" : ""}>${status}</option>`).join("")}
                    </select>
                    <button class="button light driver-availability-button" type="button" data-driver-id="${html(driver.driverId)}">Save</button>
                </div>
            </label>`;
        }).join("") || '<p class="muted">No drivers found.</p>';

        list.querySelectorAll('input[name="driver"]:not(:disabled)').forEach((input) => input.addEventListener("change", (event) => {
            state.selectedDriverId = event.target.value;
            loadDrivers();
        }));

        // Mobile browsers can preserve a radio selection while the JS state
        // is rebuilt. Sync the actual checked radio back into application state.
        const checkedDriver = list.querySelector('input[name="driver"]:checked:not(:disabled)');
        if (checkedDriver && !state.selectedDriverId) {
            state.selectedDriverId = checkedDriver.value;
        }

        list.querySelectorAll(".driver-availability-button").forEach((button) => button.addEventListener("click", async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const driverId = button.dataset.driverId;
            const select = list.querySelector(`.driver-availability[data-driver-id="${CSS.escape(driverId)}"]`);
            if (!select) return;
            setBusy(button, true);
            try {
                await apiRequest(`/drivers/${encodeURIComponent(driverId)}`, {
                    method: "PATCH",
                    body: JSON.stringify({ availabilityStatus: select.value })
                });
                setFeedback(`Driver availability updated to ${select.value}.`, true);
                await loadDrivers();
            } catch (error) {
                setFeedback(error.message);
            } finally {
                setBusy(button, false);
            }
        }));

        const selectedStillAvailable = eligible.some((driver) => driver.driverId === state.selectedDriverId);
        if (!selectedStillAvailable) {
            state.selectedDriverId = null;
        }

        // Keep the dispatch button state synchronized after the driver list is
        // rebuilt. A missing GPS location must not disable manual dispatch.
        $("#assign-button").disabled = !state.selectedRide
            || state.selectedRide.status !== "REQUESTED"
            || !state.selectedDriverId
            || !selectedStillAvailable;
    } catch (error) {
        list.innerHTML = `<p class="muted">${html(error.message)}</p>`;
        summary.textContent = "Unable to load dispatch options.";
        setFeedback(error.message);
        $("#assign-button").disabled = true;
    }
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
    // Read the selected radio directly as a fallback for mobile/browser
    // state restoration.
    const checkedDriver = document.querySelector('input[name="driver"]:checked:not(:disabled)');
    if (checkedDriver) {
        state.selectedDriverId = checkedDriver.value;
    }

    if (!state.selectedRide || !state.selectedDriverId) {
        setFeedback("Select an available driver first.");
        return;
    }
    if (state.mutationInProgress) return;

    state.mutationInProgress = true;
    const button = $("#assign-button");
    setBusy(button, true);

    try {
        await apiRequest(`/rides/${encodeURIComponent(state.selectedRide.id)}/assign?driver_id=${encodeURIComponent(state.selectedDriverId)}`, {
            method: "POST"
        });
        setFeedback("Ride dispatched to the selected driver.", true);
        state.selectedDriverId = null;
        await reloadSelected();
    } catch (error) {
        setFeedback(error.message);
        await loadDrivers();
    } finally {
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
    await mutate($("#payment-button"), `/rides/${encodeURIComponent(state.selectedRide.id)}/payment`, { paymentStatus: $("#payment-status").value, paymentReference: $("#payment-reference").value });
}
async function updateStatus(button, status) { await mutate(button, `/rides/${encodeURIComponent(state.selectedRide.id)}/status`, { status }); }

$("#alerts-button").addEventListener("click", enableAlerts);

// Use delegated click handling for the ride-alert actions so the controls
// continue to work even if the alert markup is refreshed or replaced.
document.addEventListener("click", async (event) => {
    const viewButton = event.target.closest("#view-new-ride-button");
    if (viewButton) {
        event.preventDefault();
        event.stopPropagation();
        if (!latestNewRide) return;
        const ride = latestNewRide;
        latestNewRide = null;
        $("#new-ride-alert").hidden = true;
        stopRideAlert();
        await selectRide(ride.id);
        return;
    }

    const dismissButton = event.target.closest("#dismiss-new-ride-button");
    if (dismissButton) {
        event.preventDefault();
        event.stopPropagation();
        latestNewRide = null;
        $("#new-ride-alert").hidden = true;
        stopRideAlert();
    }
});
$("#refresh-button").addEventListener("click", loadRides);
$("#status-filter").addEventListener("change", loadRides);
$("#assign-button").addEventListener("click", assignDriver);
$("#fare-button").addEventListener("click", confirmFare);
$("#payment-button").addEventListener("click", recordPayment);

let loginButtonClicked = false;

$("#login-button").addEventListener("click", () => {
    loginButtonClicked = true;
});

$("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();

    // Do not allow browser autofill, Enter-key submission, or other implicit
    // form submission to authenticate. Login requires an explicit button tap.
    if (!loginButtonClicked) return;
    loginButtonClicked = false;

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
        const session = await apiRequest("/auth/session");
        csrfToken = session?.csrfToken || null;
        showDispatcher();
        await refreshGeneratedDriverId();
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
        csrfToken = null;
        state.selectedRide = null;
        state.rides = [];
        knownRequestedRideIds.clear();
        alertsInitialized = false;
        clearInterval(alertPollTimer);
        stopRideAlert();
        showLogin("You have been logged out.");
        setBusy(button, false);
    }
});

async function initialize() {
    updateAlertsButton();
    clearInterval(alertPollTimer);
    stopRideAlert();

    // Restore an already-authenticated dispatcher session on refresh.
    // This checks the server session cookie; it does not submit the login form.
    try {
        const response = await fetch(`${API_BASE}/auth/session`, {
            credentials: "include"
        });
        if (response.ok) {
            const data = await response.json().catch(() => ({}));
            csrfToken = data?.csrfToken || null;
            if (!csrfToken) throw new Error("Dispatcher security token unavailable.");
            showDispatcher();
            await refreshGeneratedDriverId();
            await loadRides();
            await checkForNewRideRequests();
            startRideAlerts();
            return;
        }
    } catch (_) {
        // Stay on the login screen if the session check is unavailable.
    }

    showLogin();
}

initialize();
