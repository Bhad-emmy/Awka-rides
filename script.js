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
const paystackPayment = document.querySelector("#paystack-payment");
const paystackButton = document.querySelector("#paystack-button");
const paystackFare = document.querySelector("#paystack-fare");
const paystackStatus = document.querySelector("#paystack-status");
const paystackMessage = document.querySelector("#payment-message");

async function loadPaystackPayment(booking) {
    if (!paystackPayment || !booking?.reference) return;
    try {
        const response = await fetch(`${successApiBase}/rides/public/${encodeURIComponent(booking.reference)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "Unable to check payment status.");
        if (data.fareConfirmed && data.fare) {
            paystackFare.textContent = `₦${Number(data.fare).toLocaleString()}`;
        }
        if (data.status === "PAID" || data.paymentStatus === "PAID") {
            paystackStatus.textContent = "PAID";
            paystackMessage.textContent = "Payment received. Your ride can now proceed.";
            paystackButton.disabled = true;
            paystackButton.textContent = "Payment received";
            return;
        }
        if (data.status === "DRIVER ASSIGNED" && data.fareConfirmed) {
            paystackStatus.textContent = "Payment pending";
            paystackMessage.textContent = "Your rider and fare have been confirmed. You can now pay securely.";
            paystackButton.disabled = false;
        } else {
            paystackStatus.textContent = data.status || "Pending";
            paystackMessage.textContent = "Your dispatcher will confirm the fare after assigning a rider.";
            paystackButton.disabled = true;
        }
    } catch (error) {
        paystackStatus.textContent = "Unavailable";
        paystackMessage.textContent = error.message;
        paystackButton.disabled = true;
    }
}

if (paystackButton) {
    paystackButton.addEventListener("click", async () => {
        const booking = savedBooking ? JSON.parse(savedBooking) : null;
        if (!booking?.reference || !booking.email) {
            paystackMessage.textContent = "A customer email is required before payment can start.";
            return;
        }
        paystackButton.disabled = true;
        paystackButton.textContent = "Opening Paystack...";
        try {
            const response = await fetch(`${successApiBase}/payments/paystack/initialize`, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({reference: booking.reference, email: booking.email})
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.authorizationUrl) throw new Error(data.detail || "Unable to start Paystack payment.");
            window.location.href = data.authorizationUrl;
        } catch (error) {
            paystackMessage.textContent = error.message;
            paystackButton.disabled = false;
            paystackButton.textContent = "Pay securely with Paystack";
        }
    });
}
const successReference = document.querySelector("#success-reference");
if (savedBooking && successReference) {
    const booking = JSON.parse(savedBooking);
    const display = (value) => value || "Not provided";
    document.querySelector("#success-reference").textContent = display(booking.reference);
    document.querySelector("#success-name").textContent = display(booking.name);
    document.querySelector("#success-pickup").textContent = display(booking.pickup);
    document.querySelector("#success-destination").textContent = display(booking.destination);
    document.querySelector("#success-time").textContent = booking.pickupTiming === "Schedule" ? display(booking.scheduledTime).replace("T", " ") : display(booking.pickupTiming);
    const message = `Hello Ọka-Rides,\n\nI submitted a Keke ride request.\n\nBooking Reference: ${display(booking.reference)}\nName: ${display(booking.name)}\nPickup: ${display(booking.pickup)}\nDestination: ${display(booking.destination)}\nPickup Time: ${display(booking.pickupTiming === "Schedule" ? booking.scheduledTime : booking.pickupTiming)}`;
    document.querySelector("#success-whatsapp").href = `https://wa.me/2349064636239?text=${encodeURIComponent(message)}`;
}

const paystackQuery = new URLSearchParams(window.location.search);
if (successReference && savedBooking) {
    loadPaystackPayment(JSON.parse(savedBooking));
}
if (paystackQuery.get("payment") === "success") {
    if (paystackMessage) paystackMessage.textContent = "Paystack returned you after payment. Checking the verified payment status...";
    setTimeout(() => savedBooking && loadPaystackPayment(JSON.parse(savedBooking)), 500);
}
