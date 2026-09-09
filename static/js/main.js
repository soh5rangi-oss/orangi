document.querySelectorAll("[data-open-overlay]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
        var overlay = document.getElementById(trigger.dataset.openOverlay);
        if (overlay) overlay.classList.add("active");
    });
});

document.querySelectorAll("[data-close-overlay]").forEach(function (btn) {
    btn.addEventListener("click", function () {
        btn.closest(".confirm-overlay").classList.remove("active");
    });
});

document.querySelectorAll(".toggle-pw").forEach(function (toggle) {
    toggle.addEventListener("click", function () {
        var input = document.getElementById(toggle.dataset.target);
        if (!input) return;
        if (input.type === "password") {
            input.type = "text";
            toggle.textContent = "🙈";
        } else {
            input.type = "password";
            toggle.textContent = "👁️";
        }
    });
});

var slotGrid = document.getElementById("slot-grid");
var slotDataEl = document.getElementById("sleep-history-data");

if (slotGrid && slotDataEl) {
    var sleepHistory = JSON.parse(slotDataEl.textContent);
    var tempBars = document.getElementById("slot-temp-bars");

    var showSlot = function (slot) {
        var record = sleepHistory[slot];
        if (!record) return;

        document.querySelectorAll("#slot-detail [data-field]").forEach(function (el) {
            var field = el.dataset.field;
            el.textContent = record[field];
            if (el.hasAttribute("data-risk-value")) {
                el.className = "value " + record.risk_level;
            }
        });

        slotGrid.querySelectorAll(".slot-btn").forEach(function (btn) {
            btn.classList.toggle("selected", btn.dataset.slot === slot);
        });

        if (tempBars && record.temp_series) {
            var bars = tempBars.querySelectorAll(".bar");
            record.temp_series.forEach(function (point, i) {
                if (bars[i]) bars[i].style.height = point.percent + "%";
            });
        }
    };

    slotGrid.querySelectorAll(".slot-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            showSlot(btn.dataset.slot);
        });
    });

    showSlot(slotGrid.querySelector(".slot-btn").dataset.slot);
}

var videoSlotGrid = document.getElementById("video-slot-grid");

if (videoSlotGrid) {
    videoSlotGrid.querySelectorAll(".slot-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            videoSlotGrid.querySelectorAll(".slot-btn").forEach(function (b) {
                b.classList.remove("selected");
            });
            btn.classList.add("selected");
        });
    });
}
