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
        const sequence = Number(localStorage.getItem("awkaRidesBookingSequence") || "0") + 1;
        const reference = `AR-${new Date().getFullYear()}-${String(sequence).padStart(4, "0")}`;
        booking.reference = reference;
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
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || "The ride request could not be submitted.");
            }
            localStorage.setItem("awkaRidesBookingSequence", String(sequence));
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

async function loadManualPayment(booking) {
    if (!manualPayment || !booking?.reference) return;
    try {
        const response = await fetch(`${successApiBase}/rides/public/${encodeURIComponent(booking.reference)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "Unable to check ride status.");

        if (data.fareConfirmed && data.fare) {
            manualFare.textContent = `₦${Number(data.fare).toLocaleString()}`;
        }

        if (data.status === "PAID" || data.paymentStatus === "PAID") {
            manualStatus.textContent = "PAID";
            manualMessage.textContent = "Payment has been recorded. Your ride can now proceed.";
            return;
        }

        if (data.status === "DRIVER ASSIGNED" && data.fareConfirmed) {
            manualStatus.textContent = "Payment pending";
            manualMessage.textContent = "Your rider and fare have been confirmed. Please follow the manual payment instructions from the dispatcher.";
            return;
        }

        manualStatus.textContent = data.status || "Pending";
        manualMessage.textContent = "Your dispatcher will confirm the rider and fare first. Payment is currently handled manually.";
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

if (successReference && savedBooking) {
    loadManualPayment(JSON.parse(savedBooking));
}
