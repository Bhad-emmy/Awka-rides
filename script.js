const menuToggle = document.querySelector(".menu-toggle");
const navLinks = document.querySelector("#main-menu");

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
        passengers: bookingForm.elements.passengers
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
        const message = `Hello Awka Rides,\n\nI'd like to book a Keke ride.\n\nName: ${valueOf(fields.name)}\nPhone: ${valueOf(fields.phone)}\nPickup: ${valueOf(fields.pickup)}\nDestination: ${valueOf(fields.destination)}\nPickup Time: ${summaries.time.textContent}`;
        whatsappBooking.href = `https://wa.me/2349064636239?text=${encodeURIComponent(message)}`;
    }

    Object.values(fields).forEach((field) => field?.addEventListener("input", updateSummary));
    Object.values(fields).forEach((field) => field?.addEventListener("change", updateSummary));
    updateSummary();

    bookingForm.addEventListener("submit", (event) => {
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
        localStorage.setItem("awkaRidesLatestBooking", JSON.stringify(Object.fromEntries(new FormData(bookingForm))));
        formMessage.textContent = "Your request is ready for dispatcher review. Please call or WhatsApp us to confirm.";
        updateSummary();
    });
}
