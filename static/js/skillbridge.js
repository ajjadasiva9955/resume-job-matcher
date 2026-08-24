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
            const nextTheme = theme === "dark" ? "light" : "dark";
            button.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
            button.setAttribute("aria-pressed", String(theme === "light"));
        });
    }

    function formatBytes(bytes) {
        if (!bytes) return "";
        const units = ["B", "KB", "MB", "GB"];
        const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
    }

    function numericDemand(value) {
        const cleaned = String(value || "").replace(/[^\d]/g, "");
        return cleaned ? Number(cleaned) : 0;
    }

    function hydrateDemandBars() {
        const items = Array.from(document.querySelectorAll("[data-demand-count]"));
        if (!items.length) return;

        const values = items.map((item) => numericDemand(item.dataset.demandCount));
        const max = Math.max(...values, 1);

        items.forEach((item, index) => {
            const value = values[index];
            const width = value ? Math.max(18, Math.round((value / max) * 100)) : 42;
            item.style.setProperty("--demand-width", `${width}%`);
        });
    }

    window.showFileName = function showFileName() {
        const input = document.getElementById("resume");
        const fileName = document.getElementById("fileName");
        const uploadZone = document.querySelector("[data-upload-zone]");

        if (input && fileName && input.files.length > 0) {
            const file = input.files[0];
            const size = formatBytes(file.size);
            fileName.textContent = size ? `${file.name} / ${size}` : file.name;
            if (uploadZone) uploadZone.classList.add("has-file");
        }
    };

    window.showLoader = function showLoader() {
        const loader = document.getElementById("loader");
        const submit = document.querySelector("[data-submit-upload]");
        if (loader) loader.classList.add("is-visible");
        if (submit) {
            submit.setAttribute("aria-busy", "true");
            submit.innerHTML = "<span>Analyzing resume...</span>";
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
        const modalFooter = document.getElementById("modalFooter");
        if (!modal || !modalText) return;

        if (modalFooter) modalFooter.style.display = "none";
        modal.classList.add("is-open");
        modal.setAttribute("aria-hidden", "false");
        modalText.innerHTML = `<div class="thinking"><span class="thinking-dot"></span>${message || "AI Agent is thinking..."}</div>`;
    }

    function showModalError() {
        const modalText = document.getElementById("modalText");
        const modalFooter = document.getElementById("modalFooter");
        if (modalText) {
            modalText.innerHTML = '<div class="reveal">The AI response could not be loaded. Please try again.</div>';
        }
        if (modalFooter) modalFooter.style.display = "flex";
    }

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : "";
    }

    window.copyCoverLetter = async function copyCoverLetter() {
        const modalText = document.getElementById("modalText");
        const copyBtnText = document.getElementById("copyBtnText");
        const copyCoverBtn = document.getElementById("copyCoverBtn");
        if (!modalText) return;

        // Extract complete plain text while preserving formatting/line breaks
        let textToCopy = modalText.innerText || modalText.textContent || "";
        textToCopy = textToCopy.trim();

        if (!textToCopy) return;

        let success = false;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(textToCopy);
                success = true;
            } catch (err) {
                console.warn("Clipboard API failed, using fallback:", err);
            }
        }

        if (!success) {
            try {
                const textarea = document.createElement("textarea");
                textarea.value = textToCopy;
                textarea.style.position = "fixed";
                textarea.style.left = "-999999px";
                textarea.style.top = "-999999px";
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                success = document.execCommand("copy");
                document.body.removeChild(textarea);
            } catch (err) {
                console.error("Fallback copy failed:", err);
            }
        }

        if (success) {
            if (copyBtnText) copyBtnText.textContent = "✓ Copied!";
            if (copyCoverBtn) copyCoverBtn.classList.add("is-copied");
            setTimeout(() => {
                if (copyBtnText) copyBtnText.textContent = "Copy Cover Letter";
                if (copyCoverBtn) copyCoverBtn.classList.remove("is-copied");
            }, 2500);
        } else {
            alert("Unable to copy automatically. Please copy the text manually.");
        }
    };

    window.generateCover = async function generateCover(title, company, location) {
        openModal("AI Agent is generating your tailored cover letter...");

        try {
            const headers = { "Content-Type": "application/json" };
            const csrfToken = getCsrfToken();
            if (csrfToken) headers["X-CSRFToken"] = csrfToken;

            const res = await fetch("/generate-cover-letter", {
                method: "POST",
                headers: headers,
                body: JSON.stringify({ job_title: title, company: company, location: location })
            });

            const data = await res.json();
            const modalText = document.getElementById("modalText");
            const modalFooter = document.getElementById("modalFooter");
            if (modalText) {
                modalText.innerHTML = `<div class="reveal">${data.content}</div>`;
            }
            if (modalFooter) {
                modalFooter.style.display = "flex";
                const copyBtnText = document.getElementById("copyBtnText");
                if (copyBtnText) copyBtnText.textContent = "Copy Cover Letter";
            }
        } catch (error) {
            showModalError();
        }
    };

    window.generatePlan = async function generatePlan(role) {
        openModal("AI Agent is creating your personalized learning plan...");

        try {
            const headers = { "Content-Type": "application/json" };
            const csrfToken = getCsrfToken();
            if (csrfToken) headers["X-CSRFToken"] = csrfToken;

            const res = await fetch("/generate-learning-plan", {
                method: "POST",
                headers: headers,
                body: JSON.stringify({ role: role })
            });

            const data = await res.json();
            const modalText = document.getElementById("modalText");
            const modalFooter = document.getElementById("modalFooter");
            if (modalText) {
                modalText.innerHTML = `<div class="reveal">${data.content}</div>`;
            }
            if (modalFooter) {
                modalFooter.style.display = "flex";
                const copyBtnText = document.getElementById("copyBtnText");
                if (copyBtnText) copyBtnText.textContent = "Copy Plan";
            }
        } catch (error) {
            showModalError();
        }
    };

    function extractJobPayload(el) {
        const card = el ? el.closest(".job-item-card") : null;
        if (!card) return null;
        const ds = card.dataset || {};
        return {
            job_id: ds.jobId || card.getAttribute("data-job-id") || "",
            title: ds.jobTitle || card.getAttribute("data-job-title") || "",
            company_name: ds.jobCompany || card.getAttribute("data-job-company") || "",
            location: ds.jobLocation || card.getAttribute("data-job-location") || "",
            job_type: ds.jobType || card.getAttribute("data-job-type") || "Full-time",
            experience: ds.jobExperience !== undefined ? ds.jobExperience : (card.getAttribute("data-job-experience") || ""),
            salary: ds.jobSalary !== undefined ? ds.jobSalary : (card.getAttribute("data-job-salary") || "Not mentioned"),
            match_percent: ds.jobMatch !== undefined ? (parseInt(ds.jobMatch, 10) || 0) : (parseInt(card.getAttribute("data-job-match"), 10) || 0),
            posted_at: ds.jobPosted !== undefined ? ds.jobPosted : (card.getAttribute("data-job-posted") || ""),
            apply_link: ds.jobApply || card.getAttribute("data-job-apply") || "",
            company_brand: ds.jobBrand || card.getAttribute("data-job-brand") || "generic",
            openings: ds.jobOpenings || card.getAttribute("data-job-openings") || "NA"
        };
    }

    async function safeApiFetch(url, options = {}, retries = 1, timeoutMs = 25000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        const fetchOptions = { ...options, signal: controller.signal };

        try {
            const res = await fetch(url, fetchOptions);
            clearTimeout(timer);
            if (!res.ok && res.status >= 500 && retries > 0) {
                await new Promise((r) => setTimeout(r, 1000));
                return safeApiFetch(url, options, retries - 1, timeoutMs);
            }
            return res;
        } catch (err) {
            clearTimeout(timer);
            if (retries > 0 && err.name !== "AbortError") {
                await new Promise((r) => setTimeout(r, 1000));
                return safeApiFetch(url, options, retries - 1, timeoutMs);
            }
            throw err;
        }
    }

    function getNetworkErrorMessage(err) {
        if (typeof navigator !== "undefined" && navigator.onLine === false) {
            return "You appear to be offline. Please check your internet connection.";
        }
        if (err && err.name === "AbortError") {
            return "Request timed out. The server is taking longer than usual to respond. Please try again.";
        }
        return "Unable to communicate with server. Please try again in a moment.";
    }

    window.toggleSaveJob = async function toggleSaveJob(button) {
        if (!button || button.disabled) return;
        const payload = extractJobPayload(button);
        if (!payload || !payload.job_id) {
            console.error("Job payload missing or invalid.");
            return;
        }

        const isCurrentlySaved = button.classList.contains("is-saved");
        const endpoint = isCurrentlySaved ? "/remove-saved-job" : "/save-job";
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;

        button.disabled = true;
        try {
            const res = await safeApiFetch(endpoint, {
                method: "POST",
                headers: headers,
                body: JSON.stringify(payload)
            });
            const data = await res.json();

            if (res.ok && data.success) {
                const nowSaved = !isCurrentlySaved;
                button.classList.toggle("is-saved", nowSaved);
                const span = button.querySelector(".save-btn-text") || button.querySelector("span");
                if (span) {
                    span.textContent = nowSaved ? "✓ Saved" : "Save";
                }
                const icon = button.querySelector("svg");
                if (icon) {
                    icon.setAttribute("fill", nowSaved ? "currentColor" : "none");
                }
                button.setAttribute("aria-label", nowSaved ? "Remove from saved jobs" : "Save this job opportunity");

                // If on Saved Jobs page and removing, animate card removal
                if (!nowSaved && document.body.classList.contains("saved-jobs-page")) {
                    const card = button.closest(".job-item-card");
                    if (card) {
                        card.style.transition = "opacity 240ms ease, transform 240ms ease, max-height 240ms ease";
                        card.style.opacity = "0";
                        card.style.transform = "scale(0.96)";
                        setTimeout(() => {
                            card.remove();
                            checkSavedEmptyState();
                        }, 250);
                    }
                }
            } else {
                alert(data.error || "Unable to update saved job. Please try again.");
            }
        } catch (err) {
            console.error("Save job network error:", err);
            alert(getNetworkErrorMessage(err));
        } finally {
            button.disabled = false;
        }
    };

    window.toggleApplyJob = async function toggleApplyJob(button) {
        if (!button || button.disabled) return;
        const payload = extractJobPayload(button);
        if (!payload || !payload.job_id) {
            console.error("Job payload missing or invalid.");
            return;
        }

        const isCurrentlyApplied = button.classList.contains("is-applied");
        const endpoint = isCurrentlyApplied ? "/mark-not-applied" : "/mark-applied";
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;

        button.disabled = true;
        try {
            const res = await safeApiFetch(endpoint, {
                method: "POST",
                headers: headers,
                body: JSON.stringify(payload)
            });
            const data = await res.json();

            if (res.ok && data.success) {
                const nowApplied = !isCurrentlyApplied;
                button.classList.toggle("is-applied", nowApplied);
                const span = button.querySelector(".apply-btn-text") || button.querySelector("span");
                if (span) {
                    span.textContent = nowApplied ? "✓ Applied" : "Mark as Applied";
                }
                button.setAttribute("aria-label", nowApplied ? "Mark job as not applied" : "Mark job as applied");

                // If on Applied Jobs page and marking as not applied, animate card removal
                if (!nowApplied && document.body.classList.contains("applied-jobs-page")) {
                    const card = button.closest(".job-item-card");
                    if (card) {
                        card.style.transition = "opacity 240ms ease, transform 240ms ease, max-height 240ms ease";
                        card.style.opacity = "0";
                        card.style.transform = "scale(0.96)";
                        setTimeout(() => {
                            card.remove();
                            checkAppliedEmptyState();
                        }, 250);
                    }
                }
            } else {
                alert(data.error || "Unable to update application status. Please try again.");
            }
        } catch (err) {
            console.error("Apply job network error:", err);
            alert(getNetworkErrorMessage(err));
        } finally {
            button.disabled = false;
        }
    };

    window.removeSavedJobCard = async function removeSavedJobCard(button, jobId) {
        if (!jobId) return;
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;

        if (button) button.disabled = true;
        try {
            const res = await safeApiFetch("/remove-saved-job", {
                method: "POST",
                headers: headers,
                body: JSON.stringify({ job_id: jobId })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                const card = document.getElementById(`saved-card-${jobId}`) || (button ? button.closest(".job-item-card") : null);
                if (card) {
                    card.style.transition = "opacity 240ms ease, transform 240ms ease";
                    card.style.opacity = "0";
                    card.style.transform = "scale(0.96)";
                    setTimeout(() => {
                        card.remove();
                        checkSavedEmptyState();
                    }, 250);
                }
            } else {
                alert(data.error || "Unable to remove saved job. Please try again.");
                if (button) button.disabled = false;
            }
        } catch (err) {
            console.error("Remove saved job error:", err);
            alert(getNetworkErrorMessage(err));
            if (button) button.disabled = false;
        }
    };

    window.markNotAppliedCard = async function markNotAppliedCard(button, jobId) {
        if (!jobId) return;
        const headers = { "Content-Type": "application/json" };
        const csrfToken = getCsrfToken();
        if (csrfToken) headers["X-CSRFToken"] = csrfToken;

        if (button) button.disabled = true;
        try {
            const res = await safeApiFetch("/mark-not-applied", {
                method: "POST",
                headers: headers,
                body: JSON.stringify({ job_id: jobId })
            });
            const data = await res.json();

            if (res.ok && data.success) {
                const card = document.getElementById(`applied-card-${jobId}`) || (button ? button.closest(".job-item-card") : null);
                if (card) {
                    card.style.transition = "opacity 240ms ease, transform 240ms ease";
                    card.style.opacity = "0";
                    card.style.transform = "scale(0.96)";
                    setTimeout(() => {
                        card.remove();
                        checkAppliedEmptyState();
                    }, 250);
                }
            } else {
                alert(data.error || "Unable to update application status. Please try again.");
                if (button) button.disabled = false;
            }
        } catch (err) {
            console.error("Mark not applied error:", err);
            alert(getNetworkErrorMessage(err));
            if (button) button.disabled = false;
        }
    };

    function checkSavedEmptyState() {
        const list = document.getElementById("savedJobsList");
        if (list && list.querySelectorAll(".job-item-card").length === 0) {
            list.innerHTML = `
                <div class="empty-jobs-card glass-panel" id="savedEmptyState">
                    <div class="empty-jobs-icon" aria-hidden="true">
                        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#8b5cf6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                            <path d="m19 21-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path>
                        </svg>
                    </div>
                    <h2 class="empty-jobs-title">No Saved Jobs Yet</h2>
                    <p class="empty-jobs-desc">Save jobs from Find Jobs and come back here when you're ready to apply.</p>
                    <a href="/jobs" class="empty-jobs-action-btn">
                        <span>Find Jobs</span>
                        <span aria-hidden="true">&rarr;</span>
                    </a>
                </div>
            `;
        }
    }

    function checkAppliedEmptyState() {
        const list = document.getElementById("appliedJobsList");
        if (list && list.querySelectorAll(".job-item-card").length === 0) {
            list.innerHTML = `
                <div class="empty-jobs-card glass-panel" id="appliedEmptyState">
                    <div class="empty-jobs-icon icon-applied-empty" aria-hidden="true">
                        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                            <polyline points="22 4 12 14.01 9 11.01"></polyline>
                        </svg>
                    </div>
                    <h2 class="empty-jobs-title">No Applied Jobs Yet</h2>
                    <p class="empty-jobs-desc">Mark jobs as applied from Find Jobs to track your applications here.</p>
                    <a href="/jobs" class="empty-jobs-action-btn">
                        <span>Find Jobs</span>
                        <span aria-hidden="true">&rarr;</span>
                    </a>
                </div>
            `;
        }
    }

    function initApp() {
        setTheme(root.getAttribute("data-theme") || initialTheme);
        hydrateDemandBars();

        document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
            button.addEventListener("click", () => {
                setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark");
            });
        });

        // Smooth scroll for internal hash links like #contact, #jobs, #career-coach
        document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
            anchor.addEventListener("click", function (e) {
                const targetId = this.getAttribute("href");
                if (targetId && targetId.length > 1) {
                    const targetEl = document.querySelector(targetId);
                    if (targetEl) {
                        e.preventDefault();
                        targetEl.scrollIntoView({ behavior: "smooth", block: "start" });
                        // Update hash without jump
                        history.pushState(null, null, targetId);
                    }
                }
            });
        });

        const menuToggle = document.querySelector("[data-menu-toggle]");
        const navLinks = document.querySelector("[data-nav-links]");
        if (menuToggle && navLinks) {
            menuToggle.addEventListener("click", () => {
                const isOpen = navLinks.classList.toggle("is-open");
                menuToggle.setAttribute("aria-expanded", String(isOpen));
            });

            navLinks.addEventListener("click", () => {
                navLinks.classList.remove("is-open");
                menuToggle.setAttribute("aria-expanded", "false");
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

        // =====================================================================
        // PART 1: ANALYZE RESUME & FIND JOBS — LOADING EXPERIENCE CONTROLLER
        // =====================================================================
        let analysisTimer = null;
        window._isJobAnalysisRunning = false;

        function startJobAnalysisLoading() {
            const overlay = document.getElementById("jobAnalysisLoadingOverlay");
            const ctaBtn = document.getElementById("analyzeAndFindJobsBtn");

            if (window._isJobAnalysisRunning) {
                return false;
            }
            window._isJobAnalysisRunning = true;

            // 1. Immediately disable button and prevent duplicate interactions
            if (ctaBtn) {
                ctaBtn.classList.add("is-loading-disabled");
                ctaBtn.setAttribute("aria-disabled", "true");
                ctaBtn.setAttribute("tabindex", "-1");
            }

            // 2. Immediately display loading UI
            if (overlay) {
                overlay.style.display = "grid";
                overlay.classList.add("is-active");
                overlay.setAttribute("aria-hidden", "false");
            }

            // 3. Sequenced status steps & animated progress
            const progressFill = document.getElementById("analysisProgressFill");
            if (progressFill) progressFill.style.width = "12%";

            const stepDefinitions = [
                { id: "analysisStep0", duration: 3200, progress: 24 },
                { id: "analysisStep1", duration: 6000, progress: 42 },
                { id: "analysisStep2", duration: 7500, progress: 62 },
                { id: "analysisStep3", duration: 16000, progress: 82 },
                { id: "analysisStep4", duration: 14000, progress: 92 },
                { id: "analysisStep5", duration: 40000, progress: 97 }
            ];

            let activeStepIdx = 0;

            function renderStep(idx) {
                for (let i = 0; i < stepDefinitions.length; i++) {
                    const stepEl = document.getElementById(stepDefinitions[i].id);
                    if (!stepEl) continue;
                    const icon = stepEl.querySelector(".step-status-icon");

                    if (i < idx) {
                        stepEl.className = "analysis-step-item is-completed";
                        if (icon) icon.innerHTML = '<span style="font-weight:700;">✓</span>';
                    } else if (i === idx) {
                        stepEl.className = "analysis-step-item is-active";
                        if (icon) icon.innerHTML = '<span class="step-dot-spinner"></span>';
                    } else {
                        stepEl.className = "analysis-step-item is-pending";
                        if (icon) icon.innerHTML = '<span class="step-dot-bullet">→</span>';
                    }
                }
                if (progressFill && stepDefinitions[idx]) {
                    progressFill.style.width = `${stepDefinitions[idx].progress}%`;
                }
            }

            renderStep(0);

            function advanceStep() {
                if (activeStepIdx < stepDefinitions.length - 1) {
                    activeStepIdx++;
                    renderStep(activeStepIdx);
                    analysisTimer = setTimeout(advanceStep, stepDefinitions[activeStepIdx].duration);
                }
            }

            analysisTimer = setTimeout(advanceStep, stepDefinitions[0].duration);
            return true;
        }

        window.stopJobAnalysisLoading = function stopJobAnalysisLoading() {
            window._isJobAnalysisRunning = false;
            if (analysisTimer) {
                clearTimeout(analysisTimer);
                analysisTimer = null;
            }
            const overlay = document.getElementById("jobAnalysisLoadingOverlay");
            const ctaBtn = document.getElementById("analyzeAndFindJobsBtn");

            if (overlay) {
                overlay.classList.remove("is-active");
                overlay.style.display = "none";
                overlay.setAttribute("aria-hidden", "true");
            }
            if (ctaBtn) {
                ctaBtn.classList.remove("is-loading-disabled");
                ctaBtn.removeAttribute("aria-disabled");
                ctaBtn.removeAttribute("tabindex");
            }
        };

        const analyzeCta = document.getElementById("analyzeAndFindJobsBtn");
        if (analyzeCta) {
            analyzeCta.addEventListener("click", function (e) {
                if (window._isJobAnalysisRunning) {
                    e.preventDefault();
                    e.stopPropagation();
                    return false;
                }
                startJobAnalysisLoading();
            });
        }

        // Reset if user navigates back via bfcache
        window.addEventListener("pageshow", function () {
            window.stopJobAnalysisLoading();
        });


        // =====================================================================
        // PART 2: JOB LISTING FILTER TOOLBAR CONTROLLER (4 CONTROLS)
        // =====================================================================
        const filterToolbar = document.getElementById("jobFilterToolbar");
        if (filterToolbar) {
            const searchInput = document.getElementById("jobSearchInput");
            const clearSearchBtn = document.getElementById("clearSearchInputBtn");
            const locationDropdownBtn = document.getElementById("locationDropdownBtn");
            const locationDropdownMenu = document.getElementById("locationDropdownMenu");
            const locationCheckboxList = document.getElementById("locationCheckboxList");
            const locationSelectedCount = document.getElementById("locationSelectedCount");
            const clearLocationsBtn = document.getElementById("clearLocationsBtn");
            const locationDoneBtn = document.getElementById("locationDoneBtn");

            const roleDropdownBtn = document.getElementById("roleDropdownBtn");
            const roleDropdownMenu = document.getElementById("roleDropdownMenu");
            const roleCheckboxList = document.getElementById("roleCheckboxList");
            const roleSelectedCount = document.getElementById("roleSelectedCount");
            const clearRolesBtn = document.getElementById("clearRolesBtn");
            const roleDoneBtn = document.getElementById("roleDoneBtn");

            const clearAllBtn = document.getElementById("clearAllFiltersBtn");
            const statusBar = document.getElementById("filterStatusBar");
            const filterCountLabel = document.getElementById("filterResultsCount");
            const filterChipsContainer = document.getElementById("activeFilterChips");
            const noFilteredJobsState = document.getElementById("noFilteredJobsState");

            const jobCards = Array.from(document.querySelectorAll("#jobs .job-item-card"));
            const totalJobs = jobCards.length;

            /**
             * Reusable normalization function for Job Roles:
             * - Safely stringifies & trims
             * - Strips trailing parentheticals / metadata suffixes
             * - Collapses hyphens, slashes, and spaces
             * - Standardizes compound prefixes (back-end / back end -> backend)
             * - Case-insensitive lowercasing
             */
            function normalizeRoleKey(value) {
                if (value == null) return "";
                let s = String(value).trim();
                if (!s) return "";

                // Strip trailing parentheticals like "(Python / Django)" or "(Immediate Joiner)"
                s = s.replace(/\s*\([^)]*\)/g, " ");

                // Remove metadata suffixes after dashes, pipes, slashes
                s = s.replace(/\s*[-–—|/]\s*(?:flask|python|node|nodejs|node\.js|django|java|aws|remote|immediate|fresher|\d+.*|full[- ]time|contract|internship|permanent).*$/i, " ");

                // Replace all remaining punctuation/dashes/underscores with space
                s = s.replace(/[-_–—/|]+/g, " ");

                // Collapse multiple spaces and lowercase
                s = s.replace(/\s+/g, " ").trim().toLowerCase();

                // Standardize compound variations
                s = s.replace(/\bback\s+end\b/g, "backend");
                s = s.replace(/\bfront\s+end\b/g, "frontend");
                s = s.replace(/\bfull\s+stack\b/g, "fullstack");

                return s;
            }

            /**
             * Reusable normalization function for Locations:
             * - Extracts primary city part
             * - Trims, strips extra whitespace, case-insensitive
             */
            function normalizeLocationKey(value) {
                if (value == null) return "";
                let s = String(value).trim();
                if (!s) return "";

                const parts = s.split(",");
                let city = (parts[0] || "").trim();

                city = city.replace(/[-_–—]+/g, " ");
                city = city.replace(/\s+/g, " ").trim().toLowerCase();
                return city;
            }

            const CANONICAL_ROLE_LABELS = {
                "backend developer": "Backend Developer",
                "frontend developer": "Frontend Developer",
                "fullstack developer": "Full Stack Developer",
                "python developer": "Python Developer",
                "data scientist": "Data Scientist",
                "software engineer": "Software Engineer",
                "devops engineer": "DevOps Engineer",
                "qa engineer": "QA Engineer"
            };

            function getPreferredRoleLabel(normKey, variants) {
                if (CANONICAL_ROLE_LABELS[normKey]) {
                    return CANONICAL_ROLE_LABELS[normKey];
                }
                let bestVariant = "";
                let bestScore = -1;

                variants.forEach((freq, variant) => {
                    let casingScore = 0;
                    const isAllLower = variant === variant.toLowerCase();
                    const isAllUpper = variant === variant.toUpperCase() && variant.length > 3;
                    const startsWithUpper = variant[0] === variant[0].toUpperCase();

                    if (!isAllLower && !isAllUpper) casingScore += 50;
                    if (startsWithUpper) casingScore += 25;
                    if (!isAllUpper) casingScore += 10;

                    const totalScore = freq * 1000 + casingScore;
                    if (totalScore > bestScore) {
                        bestScore = totalScore;
                        bestVariant = variant;
                    }
                });

                return bestVariant || normKey;
            }

            function getPreferredLocationLabel(normKey, variants) {
                let bestVariant = "";
                let bestScore = -1;

                variants.forEach((freq, variant) => {
                    let casingScore = 0;
                    const isAllLower = variant === variant.toLowerCase();
                    const isAllUpper = variant === variant.toUpperCase() && variant.length > 3;
                    const startsWithUpper = variant[0] === variant[0].toUpperCase();

                    if (!isAllLower && !isAllUpper) casingScore += 50;
                    if (startsWithUpper) casingScore += 25;
                    if (!isAllUpper) casingScore += 10;

                    const totalScore = freq * 1000 + casingScore;
                    if (totalScore > bestScore) {
                        bestScore = totalScore;
                        bestVariant = variant;
                    }
                });

                return bestVariant || (normKey.charAt(0).toUpperCase() + normKey.slice(1));
            }

            // 1. EXTRACT UNIQUE LOCATIONS & ROLES FROM CURRENT CACHED JOB CARDS
            function extractDynamicFilterOptions() {
                const locGroupMap = new Map();
                const roleGroupMap = new Map();

                jobCards.forEach(card => {
                    const rawLoc = (card.getAttribute("data-job-location") || card.dataset.jobLocation || "").trim();
                    const rawTitle = (card.getAttribute("data-job-title") || card.dataset.jobTitle || "").trim();

                    if (rawLoc) {
                        const locKey = normalizeLocationKey(rawLoc);
                        const primaryCity = (rawLoc.split(",")[0] || "").trim();
                        if (locKey && primaryCity) {
                            if (!locGroupMap.has(locKey)) {
                                locGroupMap.set(locKey, { key: locKey, totalCount: 0, variants: new Map() });
                            }
                            const grp = locGroupMap.get(locKey);
                            grp.totalCount += 1;
                            grp.variants.set(primaryCity, (grp.variants.get(primaryCity) || 0) + 1);
                        }
                    }

                    if (rawTitle) {
                        const roleKey = normalizeRoleKey(rawTitle);
                        if (roleKey) {
                            if (!roleGroupMap.has(roleKey)) {
                                roleGroupMap.set(roleKey, { key: roleKey, totalCount: 0, variants: new Map() });
                            }
                            const grp = roleGroupMap.get(roleKey);
                            grp.totalCount += 1;
                            const cleanVariant = rawTitle.replace(/\s*\([^)]*\)/g, "").replace(/[-_–—/|]+/g, " ").trim();
                            grp.variants.set(cleanVariant, (grp.variants.get(cleanVariant) || 0) + 1);
                        }
                    }
                });

                // Populate Location Checkboxes
                if (locationCheckboxList) {
                    locationCheckboxList.innerHTML = "";
                    if (locGroupMap.size === 0) {
                        locationCheckboxList.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--text-muted);">No locations found</div>';
                    } else {
                        const locList = [];
                        locGroupMap.forEach(grp => {
                            const displayLabel = getPreferredLocationLabel(grp.key, grp.variants);
                            locList.push({ key: grp.key, label: displayLabel, count: grp.totalCount });
                        });
                        locList.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));

                        locList.forEach(item => {
                            const label = document.createElement("label");
                            label.className = "filter-checkbox-label";
                            label.innerHTML = `
                                <input type="checkbox" class="filter-checkbox-input" value="${item.key.replace(/"/g, '&quot;')}" data-display="${item.label.replace(/"/g, '&quot;')}">
                                <span class="filter-checkbox-text">${item.label}</span>
                                <span class="filter-checkbox-count">(${item.count})</span>
                            `;
                            locationCheckboxList.appendChild(label);
                        });
                    }
                }

                // Populate Job Role Checkboxes
                if (roleCheckboxList) {
                    roleCheckboxList.innerHTML = "";
                    if (roleGroupMap.size === 0) {
                        roleCheckboxList.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--text-muted);">No roles found</div>';
                    } else {
                        const roleList = [];
                        roleGroupMap.forEach(grp => {
                            const displayLabel = getPreferredRoleLabel(grp.key, grp.variants);
                            roleList.push({ key: grp.key, label: displayLabel, count: grp.totalCount });
                        });
                        roleList.sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));

                        roleList.forEach(item => {
                            const label = document.createElement("label");
                            label.className = "filter-checkbox-label";
                            label.innerHTML = `
                                <input type="checkbox" class="filter-checkbox-input" value="${item.key.replace(/"/g, '&quot;')}" data-display="${item.label.replace(/"/g, '&quot;')}">
                                <span class="filter-checkbox-text">${item.label}</span>
                                <span class="filter-checkbox-count">(${item.count})</span>
                            `;
                            roleCheckboxList.appendChild(label);
                        });
                    }
                }
            }

            extractDynamicFilterOptions();

            // Helper to get checked items from container
            function getCheckedItems(container) {
                if (!container) return [];
                const cbs = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'));
                return cbs.map(cb => ({
                    key: cb.value,
                    display: cb.getAttribute("data-display") || cb.value
                }));
            }

            // 2. CORE FILTERING FUNCTION — 100% Client-Side, 0 API Calls, Preserves Match-Score Order
            function applyJobFilters() {
                const rawSearch = searchInput ? searchInput.value : "";
                const searchVal = rawSearch.trim().toLowerCase();
                const selectedLocItems = getCheckedItems(locationCheckboxList);
                const selectedRoleItems = getCheckedItems(roleCheckboxList);
                const selectedLocKeys = selectedLocItems.map(item => item.key);
                const selectedRoleKeys = selectedRoleItems.map(item => item.key);

                // Show / hide search clear cross
                if (clearSearchBtn) {
                    clearSearchBtn.style.display = rawSearch.trim().length > 0 ? "block" : "none";
                }

                // Update location dropdown badge
                if (locationSelectedCount) {
                    if (selectedLocKeys.length > 0) {
                        locationSelectedCount.textContent = String(selectedLocKeys.length);
                        locationSelectedCount.style.display = "inline-flex";
                    } else {
                        locationSelectedCount.style.display = "none";
                    }
                }

                // Update role dropdown badge
                if (roleSelectedCount) {
                    if (selectedRoleKeys.length > 0) {
                        roleSelectedCount.textContent = String(selectedRoleKeys.length);
                        roleSelectedCount.style.display = "inline-flex";
                    } else {
                        roleSelectedCount.style.display = "none";
                    }
                }

                let visibleCount = 0;

                jobCards.forEach(card => {
                    const rawTitle = (card.getAttribute("data-job-title") || card.dataset.jobTitle || "").trim();
                    const rawLoc = (card.getAttribute("data-job-location") || card.dataset.jobLocation || "").trim();
                    const rawComp = (card.getAttribute("data-job-company") || card.dataset.jobCompany || "").trim();

                    const cardRoleKey = normalizeRoleKey(rawTitle);
                    const cardLocKey = normalizeLocationKey(rawLoc);
                    const cardTitleRawLower = rawTitle.toLowerCase();
                    const cardLocRawLower = rawLoc.toLowerCase();
                    const cardCompRawLower = rawComp.toLowerCase();

                    // Search condition
                    let matchesSearch = true;
                    if (searchVal) {
                        matchesSearch = cardTitleRawLower.includes(searchVal) || cardLocRawLower.includes(searchVal) || cardCompRawLower.includes(searchVal);
                    }

                    // Location condition (multi-select OR within locations)
                    let matchesLocation = true;
                    if (selectedLocKeys.length > 0) {
                        matchesLocation = selectedLocKeys.some(selKey => {
                            return cardLocKey === selKey || cardLocRawLower.includes(selKey) || selKey.includes(cardLocKey);
                        });
                    }

                    // Role condition (multi-select OR within roles)
                    let matchesRole = true;
                    if (selectedRoleKeys.length > 0) {
                        matchesRole = selectedRoleKeys.some(selKey => {
                            return cardRoleKey === selKey || cardRoleKey.includes(selKey) || selKey.includes(cardRoleKey);
                        });
                    }

                    const isMatch = matchesSearch && matchesLocation && matchesRole;

                    if (isMatch) {
                        card.style.display = "";
                        visibleCount++;
                    } else {
                        card.style.display = "none";
                    }
                });

                // Update No matches empty state
                if (noFilteredJobsState) {
                    noFilteredJobsState.style.display = visibleCount === 0 && totalJobs > 0 ? "block" : "none";
                }

                // Update status bar & active chips
                const isAnyFilterActive = Boolean(rawSearch.trim() || selectedLocKeys.length > 0 || selectedRoleKeys.length > 0);
                if (statusBar) {
                    if (isAnyFilterActive) {
                        statusBar.style.display = "flex";
                        if (filterCountLabel) {
                            filterCountLabel.textContent = `Showing ${visibleCount} of ${totalJobs} jobs`;
                        }

                        if (filterChipsContainer) {
                            filterChipsContainer.innerHTML = "";

                            if (rawSearch.trim()) {
                                const chip = document.createElement("span");
                                chip.className = "filter-chip";
                                chip.innerHTML = `Search: "${rawSearch.trim()}" <span class="filter-chip-remove" data-remove="search" title="Remove search filter">✕</span>`;
                                filterChipsContainer.appendChild(chip);
                            }

                            selectedLocItems.forEach(item => {
                                const chip = document.createElement("span");
                                chip.className = "filter-chip";
                                chip.innerHTML = `📍 ${item.display} <span class="filter-chip-remove" data-remove-loc="${item.key.replace(/"/g, '&quot;')}" title="Remove location filter">✕</span>`;
                                filterChipsContainer.appendChild(chip);
                            });

                            selectedRoleItems.forEach(item => {
                                const chip = document.createElement("span");
                                chip.className = "filter-chip";
                                chip.innerHTML = `💼 ${item.display} <span class="filter-chip-remove" data-remove-role="${item.key.replace(/"/g, '&quot;')}" title="Remove job role filter">✕</span>`;
                                filterChipsContainer.appendChild(chip);
                            });
                        }
                    } else {
                        statusBar.style.display = "none";
                    }
                }
            }

            // 3. RESET AND DONE FUNCTIONS
            function resetLocationFilter(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                if (locationCheckboxList) {
                    locationCheckboxList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                        cb.checked = false;
                    });
                }
                applyJobFilters();
                // Ensure dropdown remains open and elevated
                if (locationDropdownMenu) locationDropdownMenu.style.display = "flex";
                if (locationDropdownBtn) locationDropdownBtn.setAttribute("aria-expanded", "true");
                if (locationFilterDropdown) locationFilterDropdown.classList.add("is-open");
                if (filterToolbar) filterToolbar.classList.add("has-open-dropdown");
            }

            function resetRoleFilter(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                if (roleCheckboxList) {
                    roleCheckboxList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                        cb.checked = false;
                    });
                }
                applyJobFilters();
                // Ensure dropdown remains open and elevated
                if (roleDropdownMenu) roleDropdownMenu.style.display = "flex";
                if (roleDropdownBtn) roleDropdownBtn.setAttribute("aria-expanded", "true");
                if (roleFilterDropdown) roleFilterDropdown.classList.add("is-open");
                if (filterToolbar) filterToolbar.classList.add("has-open-dropdown");
            }

            function doneLocationFilter(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                if (locationDropdownMenu) locationDropdownMenu.style.display = "none";
                if (locationDropdownBtn) locationDropdownBtn.setAttribute("aria-expanded", "false");
                if (locationFilterDropdown) locationFilterDropdown.classList.remove("is-open");
                checkAndClearToolbarOpenState();
            }

            function doneRoleFilter(e) {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                if (roleDropdownMenu) roleDropdownMenu.style.display = "none";
                if (roleDropdownBtn) roleDropdownBtn.setAttribute("aria-expanded", "false");
                if (roleFilterDropdown) roleFilterDropdown.classList.remove("is-open");
                checkAndClearToolbarOpenState();
            }

            function checkAndClearToolbarOpenState() {
                const isLocOpen = locationDropdownMenu && locationDropdownMenu.style.display === "flex";
                const isRoleOpen = roleDropdownMenu && roleDropdownMenu.style.display === "flex";
                if (!isLocOpen && !isRoleOpen && filterToolbar) {
                    filterToolbar.classList.remove("has-open-dropdown");
                }
            }

            // Expose globally for inline fallbacks
            window.resetLocationFilter = resetLocationFilter;
            window.resetRoleFilter = resetRoleFilter;
            window.doneLocationFilter = doneLocationFilter;
            window.doneRoleFilter = doneRoleFilter;
            window.applyJobFilters = applyJobFilters;

            // Direct event bindings
            if (clearLocationsBtn) clearLocationsBtn.addEventListener("click", resetLocationFilter);
            if (locationDoneBtn) locationDoneBtn.addEventListener("click", doneLocationFilter);
            if (clearRolesBtn) clearRolesBtn.addEventListener("click", resetRoleFilter);
            if (roleDoneBtn) roleDoneBtn.addEventListener("click", doneRoleFilter);

            // Delegated safety net for reset and done buttons on toolbar and dropdown menus
            filterToolbar.addEventListener("click", (e) => {
                const target = e.target;
                if (!target) return;

                if (target.closest("#clearLocationsBtn, [data-action='reset-location'], .filter-reset-btn")) {
                    if (target.closest("#locationDropdownMenu") || target.id === "clearLocationsBtn" || target.dataset.action === "reset-location") {
                        resetLocationFilter(e);
                    }
                }
                if (target.closest("#clearRolesBtn, [data-action='reset-role'], .filter-reset-btn")) {
                    if (target.closest("#roleDropdownMenu") || target.id === "clearRolesBtn" || target.dataset.action === "reset-role") {
                        resetRoleFilter(e);
                    }
                }
                if (target.closest("#locationDoneBtn, [data-action='done-location']")) {
                    doneLocationFilter(e);
                } else if (target.closest("#roleDoneBtn, [data-action='done-role']")) {
                    doneRoleFilter(e);
                }
            });

            // Search input & button
            const searchIconBtn = document.getElementById("searchIconBtn");
            if (searchIconBtn) {
                searchIconBtn.addEventListener("click", () => {
                    if (searchInput) searchInput.focus();
                    applyJobFilters();
                });
            }

            if (searchInput) {
                searchInput.value = "";
                searchInput.addEventListener("input", applyJobFilters);
                searchInput.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") {
                        e.preventDefault();
                        applyJobFilters();
                    }
                });
            }

            if (clearSearchBtn) {
                clearSearchBtn.addEventListener("click", () => {
                    if (searchInput) {
                        searchInput.value = "";
                        searchInput.focus();
                        applyJobFilters();
                    }
                });
            }

            // Location dropdown toggle
            if (locationDropdownBtn && locationDropdownMenu) {
                locationDropdownBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const isOpen = locationDropdownMenu.style.display === "flex";
                    if (roleDropdownMenu) {
                        roleDropdownMenu.style.display = "none";
                        if (roleDropdownBtn) roleDropdownBtn.setAttribute("aria-expanded", "false");
                        if (roleFilterDropdown) roleFilterDropdown.classList.remove("is-open");
                    }
                    locationDropdownMenu.style.display = isOpen ? "none" : "flex";
                    locationDropdownBtn.setAttribute("aria-expanded", String(!isOpen));
                    if (locationFilterDropdown) {
                        locationFilterDropdown.classList.toggle("is-open", !isOpen);
                    }
                    if (filterToolbar) {
                        filterToolbar.classList.toggle("has-open-dropdown", !isOpen);
                    }
                });
            }

            if (locationDropdownMenu) {
                locationDropdownMenu.addEventListener("click", (e) => {
                    e.stopPropagation();
                });
            }

            if (locationCheckboxList) {
                locationCheckboxList.addEventListener("change", applyJobFilters);
            }

            // Role dropdown toggle
            if (roleDropdownBtn && roleDropdownMenu) {
                roleDropdownBtn.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const isOpen = roleDropdownMenu.style.display === "flex";
                    if (locationDropdownMenu) {
                        locationDropdownMenu.style.display = "none";
                        if (locationDropdownBtn) locationDropdownBtn.setAttribute("aria-expanded", "false");
                        if (locationFilterDropdown) locationFilterDropdown.classList.remove("is-open");
                    }
                    roleDropdownMenu.style.display = isOpen ? "none" : "flex";
                    roleDropdownBtn.setAttribute("aria-expanded", String(!isOpen));
                    if (roleFilterDropdown) {
                        roleFilterDropdown.classList.toggle("is-open", !isOpen);
                    }
                    if (filterToolbar) {
                        filterToolbar.classList.toggle("has-open-dropdown", !isOpen);
                    }
                });
            }

            if (roleDropdownMenu) {
                roleDropdownMenu.addEventListener("click", (e) => {
                    e.stopPropagation();
                });
            }

            if (roleCheckboxList) {
                roleCheckboxList.addEventListener("change", applyJobFilters);
            }

            function closeAllDropdowns() {
                if (locationDropdownMenu) locationDropdownMenu.style.display = "none";
                if (roleDropdownMenu) roleDropdownMenu.style.display = "none";
                if (locationDropdownBtn) locationDropdownBtn.setAttribute("aria-expanded", "false");
                if (roleDropdownBtn) roleDropdownBtn.setAttribute("aria-expanded", "false");
                if (locationFilterDropdown) locationFilterDropdown.classList.remove("is-open");
                if (roleFilterDropdown) roleFilterDropdown.classList.remove("is-open");
                if (filterToolbar) filterToolbar.classList.remove("has-open-dropdown");
            }

            // Close dropdowns on outside click or escape
            document.addEventListener("click", (e) => {
                let changed = false;
                if (locationDropdownMenu && locationDropdownMenu.style.display === "flex") {
                    if (!locationDropdownMenu.contains(e.target) && e.target !== locationDropdownBtn && !locationDropdownBtn.contains(e.target)) {
                        locationDropdownMenu.style.display = "none";
                        if (locationDropdownBtn) locationDropdownBtn.setAttribute("aria-expanded", "false");
                        if (locationFilterDropdown) locationFilterDropdown.classList.remove("is-open");
                        changed = true;
                    }
                }
                if (roleDropdownMenu && roleDropdownMenu.style.display === "flex") {
                    if (!roleDropdownMenu.contains(e.target) && e.target !== roleDropdownBtn && !roleDropdownBtn.contains(e.target)) {
                        roleDropdownMenu.style.display = "none";
                        if (roleDropdownBtn) roleDropdownBtn.setAttribute("aria-expanded", "false");
                        if (roleFilterDropdown) roleFilterDropdown.classList.remove("is-open");
                        changed = true;
                    }
                }
                if (changed) {
                    checkAndClearToolbarOpenState();
                }
            });

            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape") {
                    closeAllDropdowns();
                }
            });

            // 4. CLEAR ALL CONTROL
            if (clearAllBtn) {
                clearAllBtn.addEventListener("click", () => {
                    if (searchInput) searchInput.value = "";
                    if (locationCheckboxList) {
                        locationCheckboxList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
                    }
                    if (roleCheckboxList) {
                        roleCheckboxList.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
                    }
                    closeAllDropdowns();
                    applyJobFilters();
                });
            }

            // Active chips remove click handling
            if (filterChipsContainer) {
                filterChipsContainer.addEventListener("click", (e) => {
                    const removeBtn = e.target.closest(".filter-chip-remove");
                    if (!removeBtn) return;

                    if (removeBtn.dataset.remove === "search") {
                        if (searchInput) searchInput.value = "";
                    } else if (removeBtn.dataset.removeLoc) {
                        const targetLoc = removeBtn.dataset.removeLoc;
                        if (locationCheckboxList) {
                            const cb = Array.from(locationCheckboxList.querySelectorAll('input[type="checkbox"]')).find(c => c.value === targetLoc);
                            if (cb) cb.checked = false;
                        }
                    } else if (removeBtn.dataset.removeRole) {
                        const targetRole = removeBtn.dataset.removeRole;
                        if (roleCheckboxList) {
                            const cb = Array.from(roleCheckboxList.querySelectorAll('input[type="checkbox"]')).find(c => c.value === targetRole);
                            if (cb) cb.checked = false;
                        }
                    }
                    applyJobFilters();
                });
            }

            // 5. INITIALIZE IN DEFAULT RESET STATE (ALL JOBS VISIBLE, MATCH-SCORE ORDER, 0 PERSISTENCE)
            applyJobFilters();
        }

        // 6. INITIALIZE SAFE LOGOUT CONFIRMATION MODAL
        initLogoutModal();
    }

    function initLogoutModal() {
        function getOrCreateModal() {
            let modal = document.getElementById("logoutConfirmModal");
            if (!modal) {
                const wrapper = document.createElement("div");
                wrapper.innerHTML = `
<div id="logoutConfirmModal" class="logout-modal-backdrop" aria-hidden="true" role="dialog" aria-modal="true" aria-labelledby="logoutModalTitle">
    <div class="logout-modal-card glass-modal" role="document">
        <button type="button" class="logout-modal-close-btn" id="logoutModalCloseBtn" aria-label="Close modal" title="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        </button>
        <div class="logout-modal-icon-wrapper">
            <div class="logout-modal-icon-glow"></div>
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                <polyline points="16 17 21 12 16 7"></polyline>
                <line x1="21" y1="12" x2="9" y2="12"></line>
            </svg>
        </div>
        <h3 id="logoutModalTitle" class="logout-modal-title">Are you sure you want to logout?</h3>
        <p class="logout-modal-desc">You will need to sign in again to access your SkillBridge.AI workspace.</p>
        <div class="logout-modal-actions">
            <button type="button" class="logout-modal-btn logout-modal-btn-cancel" id="logoutModalCancelBtn">
                No
            </button>
            <a href="/logout" class="logout-modal-btn logout-modal-btn-confirm" id="logoutModalConfirmBtn">
                Yes, Logout
            </a>
        </div>
    </div>
</div>`;
                document.body.appendChild(wrapper.firstElementChild);
                modal = document.getElementById("logoutConfirmModal");
            }
            return modal;
        }

        function openLogoutModal() {
            const modal = getOrCreateModal();
            if (modal) {
                modal.classList.add("active");
                modal.classList.add("is-open");
                modal.setAttribute("aria-hidden", "false");
                const cancelBtn = document.getElementById("logoutModalCancelBtn");
                if (cancelBtn) cancelBtn.focus();
            }
        }

        function closeLogoutModal() {
            const modal = document.getElementById("logoutConfirmModal");
            if (modal) {
                modal.classList.remove("active");
                modal.classList.remove("is-open");
                modal.setAttribute("aria-hidden", "true");
            }
        }

        // Intercept logout triggers
        document.addEventListener("click", function (e) {
            const logoutTrigger = e.target.closest('[data-logout-trigger="true"], .bottom-nav-logout-tab, a[href="/logout"]');
            if (logoutTrigger) {
                // If it's the confirm button inside the modal, allow navigation to proceed
                if (e.target.closest("#logoutModalConfirmBtn") || logoutTrigger.id === "logoutModalConfirmBtn") {
                    return;
                }
                e.preventDefault();
                e.stopPropagation();
                openLogoutModal();
                return;
            }

            // Close button inside modal
            if (e.target.closest("#logoutModalCloseBtn") || e.target.closest("#logoutModalCancelBtn")) {
                e.preventDefault();
                closeLogoutModal();
                return;
            }

            // Backdrop click (click outside modal card)
            const modal = document.getElementById("logoutConfirmModal");
            if (modal && (modal.classList.contains("active") || modal.classList.contains("is-open")) && e.target === modal) {
                closeLogoutModal();
            }
        });

        // Close on Escape key
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" || e.keyCode === 27) {
                const modal = document.getElementById("logoutConfirmModal");
                if (modal && (modal.classList.contains("active") || modal.classList.contains("is-open"))) {
                    e.preventDefault();
                    closeLogoutModal();
                }
            }
        });

        // Expose global helpers
        window.openLogoutModal = openLogoutModal;
        window.closeLogoutModal = closeLogoutModal;
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initApp);
    } else {
        initApp();
    }
})();

