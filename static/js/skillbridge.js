(function () {
    const root = document.documentElement;
    const storedTheme = localStorage.getItem("skillbridge-theme");
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    const initialTheme = storedTheme || (prefersLight ? "light" : "dark");

    root.setAttribute("data-theme", initialTheme);

    function setTheme(theme) {
        root.setAttribute("data-theme", theme);
        localStorage.setItem("skillbridge-theme", theme);
        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
        });
    }

    function formatBytes(bytes) {
        if (!bytes) return "";
        const units = ["B", "KB", "MB", "GB"];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
    }

    window.showFileName = function showFileName() {
        const input = document.getElementById("resume");
        const fileName = document.getElementById("fileName");
        const uploadZone = document.querySelector("[data-upload-zone]");

        if (input && fileName && input.files.length > 0) {
            const file = input.files[0];
            const size = formatBytes(file.size);
            fileName.textContent = size ? `${file.name} • ${size}` : file.name;
            if (uploadZone) uploadZone.classList.add("has-file");
        }
    };

    window.showLoader = function showLoader() {
        const loader = document.getElementById("loader");
        const submit = document.querySelector("[data-submit-upload]");
        if (loader) loader.classList.add("is-visible");
        if (submit) {
            submit.setAttribute("aria-busy", "true");
            submit.textContent = "Analyzing resume...";
        }
    };

    window.closeModal = function closeModal() {
        const modal = document.getElementById("modal");
        if (modal) {
            modal.classList.remove("is-open");
            modal.setAttribute("aria-hidden", "true");
        }
    };

    function openModal(message) {
        const modal = document.getElementById("modal");
        const modalText = document.getElementById("modalText");
        if (!modal || !modalText) return;

        modal.classList.add("is-open");
        modal.setAttribute("aria-hidden", "false");
        modalText.innerHTML = `<div class="thinking"><span class="thinking-dot"></span>${message || "AI Agent is thinking..."}</div>`;
    }

    window.generateCover = async function generateCover(title, company, location) {
        openModal("AI Agent is thinking...");

        const res = await fetch("/generate-cover-letter", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_title: title, company: company, location: location })
        });

        const data = await res.json();
        const modalText = document.getElementById("modalText");
        if (modalText) modalText.innerHTML = `<div class="reveal">${data.content}</div>`;
    };

    window.generatePlan = async function generatePlan(role) {
        openModal("AI Agent is thinking...");

        const res = await fetch("/generate-learning-plan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: role })
        });

        const data = await res.json();
        const modalText = document.getElementById("modalText");
        if (modalText) modalText.innerHTML = `<div class="reveal">${data.content}</div>`;
    };

    document.addEventListener("DOMContentLoaded", () => {
        setTheme(root.getAttribute("data-theme") || initialTheme);

        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
            });
        });

        const menuToggle = document.querySelector("[data-menu-toggle]");
        const navLinks = document.querySelector("[data-nav-links]");
        if (menuToggle && navLinks) {
            menuToggle.addEventListener("click", () => {
                const isOpen = navLinks.classList.toggle("is-open");
                menuToggle.setAttribute("aria-expanded", String(isOpen));
            });
        }

        const uploadZone = document.querySelector("[data-upload-zone]");
        const resumeInput = document.getElementById("resume");
        if (uploadZone && resumeInput) {
            ["dragenter", "dragover"].forEach((eventName) => {
                uploadZone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    uploadZone.classList.add("is-dragover");
                });
            });

            ["dragleave", "drop"].forEach((eventName) => {
                uploadZone.addEventListener(eventName, () => {
                    uploadZone.classList.remove("is-dragover");
                });
            });

            uploadZone.addEventListener("drop", (event) => {
                event.preventDefault();
                if (event.dataTransfer.files.length) {
                    try {
                        resumeInput.files = event.dataTransfer.files;
                        window.showFileName();
                    } catch (error) {
                        uploadZone.classList.add("has-file");
                    }
                }
            });
        }

        const modal = document.getElementById("modal");
        if (modal) {
            modal.addEventListener("click", (event) => {
                if (event.target === modal) window.closeModal();
            });

            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape") window.closeModal();
            });
        }
    });
})();
