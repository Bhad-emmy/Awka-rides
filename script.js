const bookingForm = document.querySelector("#booking-form");
const successCard = document.querySelector("#success");
const requestSummary = document.querySelector("#request-summary");
const pickupTiming = document.querySelector("#pickup-timing");
const scheduledTimeField = document.querySelector("#scheduled-time-field");
const scheduledTime = document.querySelector("#scheduled-time");
const formMessage = document.querySelector("#form-message");
const mapPreview = document.querySelector("#map-preview");
const pickupLocation = document.querySelector("#pickup-location");
const menuToggle = document.querySelector(".menu-toggle");
const navLinks = document.querySelector("#main-menu");

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

const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
        if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            revealObserver.unobserve(entry.target);
        }
    });
}, { threshold: 0.12 });

document.querySelectorAll(".reveal").forEach((element) => revealObserver.observe(element));

function setMinimumScheduledTime() {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    scheduledTime.min = now.toISOString().slice(0, 16);
}

setMinimumScheduledTime();

mapPreview.addEventListener("click", () => {
    pickupLocation.value = "Aroma Junction";
    pickupLocation.focus();
});

function createRequestId() {
    return `AWK-${Date.now().toString(36).toUpperCase()}`;
}

bookingForm.addEventListener("submit", (event) => {
    event.preventDefault();
    formMessage.textContent = "";

    const requiredFields = bookingForm.querySelectorAll("[required]");
    requiredFields.forEach((field) => {
        field.setCustomValidity(field.value.trim() ? "" : "Please complete this field.");
    });

    const phone = bookingForm.elements.phone;
    if (phone.value.trim() && !/^[0-9+\s()-]{7,20}$/.test(phone.value.trim())) {
        phone.setCustomValidity("Please enter a valid phone number.");
    }

    if (!bookingForm.checkValidity()) {
        formMessage.textContent = "Please complete the highlighted fields before submitting.";
        bookingForm.reportValidity();
        return;
    }

    const formData = new FormData(bookingForm);
    const booking = {
        id: createRequestId(),
        pickup: formData.get("pickup"),
        destination: formData.get("destination"),
        vehicle: formData.get("vehicle"),
        pickupTiming: formData.get("pickupTiming"),
        scheduledTime: formData.get("scheduledTime"),
        name: formData.get("name"),
        phone: formData.get("phone"),
        notes: formData.get("notes")
    };

    localStorage.setItem("awkaRidesLatestBooking", JSON.stringify(booking));
    requestSummary.textContent =
        `Request ${booking.id} is saved for ${booking.name}. We will call ${booking.phone} to confirm your ${booking.vehicle.toLowerCase()} ride from ${booking.pickup} to ${booking.destination}.`;

    bookingForm.hidden = true;
    successCard.hidden = false;
    successCard.scrollIntoView({ behavior: "smooth", block: "start" });
});