const menuToggle = document.querySelector(".menu-toggle");
const navLinks = document.querySelector("#main-menu");
const API_BASE = document.querySelector('meta[name="awka-api-base"]')?.content || "/api";

if (menuToggle && navLinks) {
    menuToggle.addEventListener("click", () => {
        const isOpen = navLinks.classList.toggle("open");
        menuToggle.setAttribute("aria-expanded", String(isOpen));
    });

    navLinks.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => {
            navLinks.classList.remove("open");
            menuToggle.setAttribute("aria-expanded", "false");
        });
    });
}

const revealElements = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window) {
    const revealObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add("visible");
                revealObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12 });
    revealElements.forEach((element) => revealObserver.observe(element));
} else {
    revealElements.forEach((element) => element.classList.add("visible"));
}

const bookingForm = document.querySelector("#booking-form");
if (bookingForm) {
    const pickupTiming = document.querySelector("#pickup-timing");
    const scheduledTimeField = document.querySelector("#scheduled-time-field");
    const scheduledTime = document.querySelector("#scheduled-time");
    const formMessage = document.querySelector("#form-message");
    const whatsappBooking = document.querySelector("#whatsapp-booking");
    const mapElement = document.querySelector("#booking-map");
    const pickupLatitude = bookingForm.elements.pickupLatitude;
    const pickupLongitude = bookingForm.elements.pickupLongitude;
    const destinationLatitude = bookingForm.elements.destinationLatitude;
    const destinationLongitude = bookingForm.elements.destinationLongitude;

    if (scheduledTime) {
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        scheduledTime.min = now.toISOString().slice(0, 16);
    }

    pickupTiming?.addEventListener("change", () => {
        const isScheduled = pickupTiming.value === "Schedule";
        scheduledTimeField.hidden = !isScheduled;
        scheduledTime.required = isScheduled;
        if (!isScheduled) scheduledTime.value = "";
    });

    const fields = {
        name: bookingForm.elements.name,
        phone: bookingForm.elements.phone,
        pickup: bookingForm.elements.pickup,
        destination: bookingForm.elements.destination,
        pickupTiming,
        scheduledTime,
        passengers: bookingForm.elements.passengers,
        email: bookingForm.elements.email
    };
    const summaries = {
        name: document.querySelector("#summary-name"),
        pickup: document.querySelector("#summary-pickup"),
        destination: document.querySelector("#summary-destination"),
        time: document.querySelector("#summary-time")
    };

    function valueOf(field) {
        return field?.value.trim() || "Not provided";
    }

    function updateSummary() {
        summaries.name.textContent = valueOf(fields.name);
        summaries.pickup.textContent = valueOf(fields.pickup);
        summaries.destination.textContent = valueOf(fields.destination);
        summaries.time.textContent = fields.pickupTiming.value === "Schedule" && fields.scheduledTime.value
            ? fields.scheduledTime.value.replace("T", " ")
            : valueOf(fields.pickupTiming);
        const message = `Hello Ọka-Rides,\n\nI'd like to book a Keke ride.\n\nName: ${valueOf(fields.name)}\nPhone: ${valueOf(fields.phone)}\nPickup: ${valueOf(fields.pickup)}\nDestination: ${valueOf(fields.destination)}\nPickup Time: ${summaries.time.textContent}`;
        whatsappBooking.href = `https://wa.me/2349064636239?text=${encodeURIComponent(message)}`;
    }

    Object.values(fields).forEach((field) => field?.addEventListener("input", updateSummary));
    Object.values(fields).forEach((field) => field?.addEventListener("change", updateSummary));
    updateSummary();

    if (mapElement && window.L) {
        const map = L.map(mapElement).setView([6.2088, 7.0719], 13);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors"
        }).addTo(map);
        let selectionMode = "pickup";
        let pickupMarker;
        let destinationMarker;
        const pickupButton = document.querySelector("#select-pickup-location");
        const destinationButton = document.querySelector("#select-destination-location");
        const selectionStatus = document.querySelector("#map-selection-status");
        const pickupCoordinates = document.querySelector("#pickup-coordinates");
        const destinationCoordinates = document.querySelector("#destination-coordinates");

        function setSelectionMode(mode) {
            selectionMode = mode;
            pickupButton.classList.toggle("active", mode === "pickup");
            destinationButton.classList.toggle("active", mode === "destination");
            selectionStatus.textContent = mode === "pickup"
                ? "Selecting pickup: click the map to place a marker."
                : "Selecting destination: click the map to place a marker.";
        }

        function setLocation(location, latitude, longitude) {
            const coordinates = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
            if (location === "pickup") {
                pickupLatitude.value = latitude;
                pickupLongitude.value = longitude;
                if (pickupMarker) pickupMarker.setLatLng([latitude, longitude]);
                else pickupMarker = L.marker([latitude, longitude]).addTo(map).bindPopup("Pickup location").openPopup();
                pickupCoordinates.textContent = `Pickup: ${coordinates}`;
            } else {
                destinationLatitude.value = latitude;
                destinationLongitude.value = longitude;
                if (destinationMarker) destinationMarker.setLatLng([latitude, longitude]);
                else destinationMarker = L.marker([latitude, longitude]).addTo(map).bindPopup("Destination location").openPopup();
                destinationCoordinates.textContent = `Destination: ${coordinates}`;
            }
        }

        function resetLocation(location) {
            if (location === "pickup") {
                pickupLatitude.value = "";
                pickupLongitude.value = "";
                if (pickupMarker) map.removeLayer(pickupMarker);
                pickupMarker = null;
                pickupCoordinates.textContent = "Pickup: not selected";
            } else {
                destinationLatitude.value = "";
                destinationLongitude.value = "";
                if (destinationMarker) map.removeLayer(destinationMarker);
                destinationMarker = null;
                destinationCoordinates.textContent = "Destination: not selected";
            }
        }

        pickupButton.addEventListener("click", () => setSelectionMode("pickup"));
        destinationButton.addEventListener("click", () => setSelectionMode("destination"));
        document.querySelector("#reset-pickup-location").addEventListener("click", () => resetLocation("pickup"));
        document.querySelector("#reset-destination-location").addEventListener("click", () => resetLocation("destination"));
        map.on("click", (event) => setLocation(selectionMode, event.latlng.lat, event.latlng.lng));
    }

    bookingForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        formMessage.textContent = "";
        bookingForm.querySelectorAll("[required]").forEach((field) => field.setCustomValidity(field.value.trim() ? "" : "Please complete this field."));
        const phone = fields.phone;
        if (phone.value.trim() && !/^[0-9+\s()-]{7,20}$/.test(phone.value.trim())) phone.setCustomValidity("Please enter a valid phone number.");
        if (!bookingForm.checkValidity()) {
            formMessage.textContent = "Please complete the highlighted fields before submitting.";
            bookingForm.reportValidity();
            return;
        }
        const booking = Object.fromEntries(new FormData(bookingForm));
        // Optional map coordinates are represented by empty form values when no marker is selected.
        // Convert those empty strings to null so FastAPI accepts the optional float fields.
        ["pickupLatitude", "pickupLongitude", "destinationLatitude", "destinationLongitude"].forEach((field) => {
            if (booking[field] === "") booking[field] = null;
        });
        // The API generates the booking reference server-side.
        delete booking.reference;
        const submitButton = bookingForm.querySelector("button[type='submit']");
        submitButton.disabled = true;
        submitButton.setAttribute("aria-busy", "true");
        formMessage.textContent = "Submitting your ride request...";

        try {
            const response = await fetch(`${API_BASE}/rides`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(booking)
            });
            const created = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(created.detail || "The ride request could not be submitted.");
            }
            if (!created.reference) {
                throw new Error("The booking service did not return a booking reference.");
            }
            booking.reference = created.reference;
            localStorage.setItem("awkaRidesLatestBooking", JSON.stringify(booking));
            window.location.href = "success.html";
        } catch (error) {
            formMessage.textContent = error instanceof TypeError
                ? "Unable to reach the booking service. Please check your connection and try again."
                : error.message;
            submitButton.disabled = false;
            submitButton.removeAttribute("aria-busy");
        }
    });
}

const savedBooking = localStorage.getItem("awkaRidesLatestBooking");
const successApiBase = document.querySelector('meta[name="awka-api-base"]')?.content || "/api";
const manualPayment = document.querySelector("#manual-payment");
const manualFare = document.querySelector("#manual-fare");
const manualStatus = document.querySelector("#manual-status");
const manualMessage = document.querySelector("#payment-message");

const assignedRider = document.querySelector("#assigned-rider");
const riderName = document.querySelector("#rider-name");
const riderPhone = document.querySelector("#rider-phone");
const riderVehicle = document.querySelector("#rider-vehicle");
const riderPlate = document.querySelector("#rider-plate");
const callRider = document.querySelector("#call-rider");
const whatsappRider = document.querySelector("#whatsapp-rider");
const statusTimeline = document.querySelector(".status-timeline");

function normalizeWhatsappNumber(phone) {
    const digits = String(phone || "").replace(/\D/g, "");
    if (digits.startsWith("234") && digits.length === 13) return digits;
    if (digits.startsWith("0") && digits.length === 11) return `234${digits.slice(1)}`;
    return digits;
}

function renderAssignedRider(data, booking) {
    if (!assignedRider) return;

    const visible = data.driverAssigned && data.driverPhone;
    assignedRider.hidden = !visible;
    if (!visible) return;

    riderName.textContent = data.driverName || "Assigned rider";
    riderPhone.textContent = data.driverPhone;
    riderVehicle.textContent = data.driverVehicle || "Keke";
    riderPlate.textContent = data.driverPlateNumber || "Not provided";
    callRider.href = `tel:${data.driverPhone}`;

    const whatsappNumber = normalizeWhatsappNumber(data.driverPhone);
    const whatsappMessage = [
        "Hello, I am the customer for Ọka-Rides.",
        `Booking Reference: ${booking.reference}`,
        `Pickup landmark/location: ${booking.pickup}`,
        `Destination: ${booking.destination}`,
        "I am calling/message you to explain the exact pickup point."
    ].join("\n");
    whatsappRider.href = whatsappNumber
        ? `https://wa.me/${whatsappNumber}?text=${encodeURIComponent(whatsappMessage)}`
        : "#";
}

function updateStatusTimeline(status) {
    if (!statusTimeline) return;
    const steps = [...statusTimeline.querySelectorAll("li")];
    const currentIndex = {
        "REQUESTED": 0,
        "DRIVER ASSIGNED": 2,
        "PAID": 2,
        "PICKED UP": 3,
        "COMPLETED": 4
    }[status] ?? 0;

    steps.forEach((step, index) => {
        const isCurrentOrDone = index <= currentIndex;
        step.classList.toggle("current", isCurrentOrDone);
        const marker = step.querySelector("span");
        if (marker) marker.textContent = isCurrentOrDone ? "✓" : "○";
    });
}

async function loadManualPayment(booking) {
    if (!manualPayment || !booking?.reference) return;
    try {
        const response = await fetch(`${successApiBase}/customer/rides/${encodeURIComponent(booking.reference)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "Unable to check ride status.");

        if (data.fareConfirmed && data.fare) {
            manualFare.textContent = `₦${Number(data.fare).toLocaleString()}`;
        }

        renderAssignedRider(data, booking);
        updateStatusTimeline(data.status);

        if (data.status === "PAID" || data.paymentStatus === "PAID") {
            manualStatus.textContent = "PAID";
            manualMessage.textContent = "Payment has been recorded. Your ride can now proceed.";
        } else if (data.status === "DRIVER ASSIGNED" && data.fareConfirmed) {
            manualStatus.textContent = "Payment pending";
            manualMessage.textContent = "Your rider has been assigned and their contact details are shown above. Call the rider to explain your exact pickup point or landmark.";
        } else {
            manualStatus.textContent = data.status || "Pending";
            manualMessage.textContent = "Your dispatcher will confirm the rider and fare first. Payment is currently handled manually.";
        }
    } catch (error) {
        manualStatus.textContent = "Unavailable";
        manualMessage.textContent = error.message;
    }
}

const successReference = document.querySelector("#success-reference");
if (savedBooking && successReference) {
    const booking = JSON.parse(savedBooking);
    const display = (value) => value || "Not provided";
    successReference.textContent = display(booking.reference);
    document.querySelector("#success-name").textContent = display(booking.name);
    document.querySelector("#success-pickup").textContent = display(booking.pickup);
    document.querySelector("#success-destination").textContent = display(booking.destination);
    document.querySelector("#success-time").textContent = booking.pickupTiming === "Schedule"
        ? display(booking.scheduledTime).replace("T", " ")
        : display(booking.pickupTiming);

    const message = `Hello Ọka-Rides,

I submitted a Keke ride request.

Booking Reference: ${display(booking.reference)}
Name: ${display(booking.name)}
Pickup: ${display(booking.pickup)}
Destination: ${display(booking.destination)}
Pickup Time: ${display(booking.pickupTiming === "Schedule" ? booking.scheduledTime : booking.pickupTiming)}`;
    document.querySelector("#success-whatsapp").href = `https://wa.me/2349064636239?text=${encodeURIComponent(message)}`;
}

let statusPollTimer;
if (successReference && savedBooking) {
    const booking = JSON.parse(savedBooking);
    loadManualPayment(booking);
    statusPollTimer = window.setInterval(() => loadManualPayment(booking), 10000);
}

const accountPage = document.querySelector("#account-page");
if (accountPage) {
    const accountMessage = document.querySelector("#account-message");
    const authPanel = document.querySelector("#account-auth");
    const profilePanel = document.querySelector("#account-profile");
    const loginForm = document.querySelector("#customer-login-form");
    const signupForm = document.querySelector("#customer-signup-form");
    const loginTab = document.querySelector("#login-tab");
    const signupTab = document.querySelector("#signup-tab");
    const ridesList = document.querySelector("#customer-rides");
    const accountName = document.querySelector("#account-name");
    const accountEmail = document.querySelector("#account-email");
    const accountPhone = document.querySelector("#account-phone");
    const logoutButton = document.querySelector("#customer-logout");
    const returnTarget = new URLSearchParams(window.location.search).get("return");

    function setAccountMessage(message = "", isError = false) {
        if (!accountMessage) return;
        accountMessage.textContent = message;
        accountMessage.classList.toggle("error", isError);
    }

    function showAuthTab(tab) {
        loginForm.hidden = tab !== "login";
        signupForm.hidden = tab !== "signup";
        loginTab.classList.toggle("active", tab === "login");
        signupTab.classList.toggle("active", tab === "signup");
        setAccountMessage("");
    }

    function formatDate(value) {
        if (!value) return "Date unavailable";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" });
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (char) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
        }[char]));
    }

    function rideStatusClass(status) {
        return String(status || "").toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-|-$/g, "");
    }

    function ridePayload(ride) {
        return encodeURIComponent(JSON.stringify({
            reference: ride.reference || "",
            name: ride.name || "",
            phone: ride.phone || "",
            email: ride.email || "",
            pickup: ride.pickup || "",
            destination: ride.destination || "",
            pickupTiming: ride.pickupTiming || "Now",
            scheduledTime: ride.scheduledTime || "",
            passengers: ride.passengers || "1",
            notes: ride.notes || "",
            vehicle: ride.vehicle || "Keke"
        }));
    }

    function renderRides(rides) {
        if (!ridesList) return;
        if (!rides.length) {
            ridesList.innerHTML = '<div class="empty-history"><h3>No ride history yet</h3><p>Your previous bookings will appear here, ready to book again.</p><a class="button button-primary" href="booking.html">Book Your First Ride</a></div>';
            return;
        }

        ridesList.innerHTML = rides.map((ride) => {
            const fare = ride.fare ? "₦" + Number(ride.fare).toLocaleString() : "Fare pending";
            const status = escapeHtml(ride.status || "REQUESTED");
            const payload = ridePayload(ride);
            return '<article class="ride-history-card">' +
                '<div class="ride-history-top"><div><p class="eyebrow">Booking ' + escapeHtml(ride.reference) + '</p><h3>' + escapeHtml(ride.destination || "Ride") + '</h3></div>' +
                '<span class="ride-status ' + rideStatusClass(status) + '">' + status + '</span></div>' +
                '<div class="ride-history-route"><div><span>From</span><strong>' + escapeHtml(ride.pickup || "Not provided") + '</strong></div>' +
                '<div><span>To</span><strong>' + escapeHtml(ride.destination || "Not provided") + '</strong></div></div>' +
                '<div class="ride-history-meta"><span>' + formatDate(ride.createdAt) + '</span><strong>' + fare + '</strong></div>' +
                '<div class="ride-history-actions"><button class="button button-primary book-again-button" type="button" data-rebook="' + payload + '">↻ Book Again</button>' +
                '<button class="button button-secondary view-ride-button" type="button" data-view-ride="' + payload + '">View Status</button></div></article>';
        }).join("");

        ridesList.querySelectorAll(".book-again-button").forEach((button) => {
            button.addEventListener("click", () => {
                const payload = JSON.parse(decodeURIComponent(button.dataset.rebook));
                localStorage.setItem("okaRidesRebook", JSON.stringify(payload));
                window.location.href = "booking.html";
            });
        });

        ridesList.querySelectorAll(".view-ride-button").forEach((button) => {
            button.addEventListener("click", () => {
                const payload = JSON.parse(decodeURIComponent(button.dataset.viewRide));
                localStorage.setItem("awkaRidesLatestBooking", JSON.stringify(payload));
                window.location.href = "success.html";
            });
        });
    }

    async function loadCustomerAccount() {
        try {
            const response = await fetch(API_BASE + "/customer/auth/session");
            if (!response.ok) throw new Error("not authenticated");
            const data = await response.json();
            const customer = data.customer || {};

            if (returnTarget === "booking.html") {
                window.location.href = "booking.html";
                return true;
            }

            authPanel.hidden = true;
            profilePanel.hidden = false;
            accountName.textContent = customer.fullName || "-";
            accountEmail.textContent = customer.email || "-";
            accountPhone.textContent = customer.phone || "-";
            setAccountMessage("");

            const ridesResponse = await fetch(API_BASE + "/customer/rides");
            const ridesData = await ridesResponse.json().catch(() => ({}));
            if (!ridesResponse.ok) throw new Error(ridesData.detail || "Unable to load ride history.");
            renderRides(ridesData.rides || []);
            return true;
        } catch {
            authPanel.hidden = false;
            profilePanel.hidden = true;
            return false;
        }
    }

    async function authenticate(path, form, loadingMessage) {
        setAccountMessage(loadingMessage);
        try {
            const response = await fetch(API_BASE + path, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(Object.fromEntries(new FormData(form)))
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || "Authentication failed.");
            if (returnTarget === "booking.html") {
                window.location.href = "booking.html";
                return;
            }
            await loadCustomerAccount();
        } catch (error) {
            setAccountMessage(error.message, true);
        }
    }

    loginForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        await authenticate("/customer/auth/login", loginForm, "Signing you in...");
    });

    signupForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        await authenticate("/customer/auth/signup", signupForm, "Creating your account...");
    });

    loginTab?.addEventListener("click", () => showAuthTab("login"));
    signupTab?.addEventListener("click", () => showAuthTab("signup"));

    logoutButton?.addEventListener("click", async () => {
        await fetch(API_BASE + "/customer/auth/logout", { method: "POST" });
        window.location.reload();
    });

    showAuthTab("login");
    loadCustomerAccount();
}

const bookingCustomerGate = document.querySelector("#customer-gate");
if (bookingForm && bookingCustomerGate) {
    const gateLink = document.querySelector("#customer-gate-link");
    const requestButton = bookingForm.querySelector("button[type='submit']");

    async function loadBookingCustomer() {
        try {
            const response = await fetch(API_BASE + "/customer/auth/session");
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || "Please sign in.");

            const customer = data.customer || {};
            bookingCustomerGate.hidden = true;
            requestButton.disabled = false;

            const nameField = bookingForm.elements.name;
            const phoneField = bookingForm.elements.phone;
            const emailField = bookingForm.elements.email;

            nameField.value = customer.fullName || "";
            phoneField.value = customer.phone || "";
            emailField.value = customer.email || "";

            [nameField, phoneField, emailField].forEach((field) => {
                field.readOnly = true;
                field.classList.add("readonly-field");
            });

            bookingForm.elements.destination.dispatchEvent(new Event("input", { bubbles: true }));
        } catch {
            bookingCustomerGate.hidden = false;
            requestButton.disabled = true;
            gateLink.href = "account.html?return=booking.html";
        }
    }

    loadBookingCustomer();

    const rebook = JSON.parse(localStorage.getItem("okaRidesRebook") || "null");
    if (rebook) {
        const destinationField = bookingForm.elements.destination;
        const pickupField = bookingForm.elements.pickup;
        const passengersField = bookingForm.elements.passengers;
        const vehicleField = bookingForm.elements.vehicle;
        const notesField = bookingForm.elements.notes;
        const timingField = bookingForm.elements.pickupTiming;

        if (pickupField && rebook.pickup) pickupField.value = rebook.pickup;
        if (destinationField && rebook.destination) destinationField.value = rebook.destination;
        if (passengersField && rebook.passengers) passengersField.value = String(rebook.passengers);
        if (vehicleField && rebook.vehicle) vehicleField.value = rebook.vehicle;
        if (notesField && rebook.notes) notesField.value = rebook.notes;
        if (timingField) timingField.value = "Now";

        localStorage.removeItem("okaRidesRebook");
        bookingForm.elements.destination.dispatchEvent(new Event("input", { bubbles: true }));
    }
}
