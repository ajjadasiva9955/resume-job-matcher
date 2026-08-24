/**
 * JARVIS GLOBAL AI CHATBOT CONTROLLER — SKILLBRIDGE.AI
 * Manages UI interactions, messaging, attachments, page context, and API dispatch.
 */

(function () {
    "use strict";

    // DOM Elements
    let triggerWrap = null;
    let openBtn = null;
    let chatPanel = null;
    let closeBtn = null;
    let contactBtn = null;
    let contactModal = null;
    let contactModalClose = null;
    let contactModalBackdrop = null;
    let messagesList = null;
    let typingRow = null;
    let form = null;
    let textarea = null;
    let sendBtn = null;
    let attachBtn = null;
    let fileInput = null;
    let attachmentPreview = null;
    let attachmentNameEl = null;
    let attachmentRemoveBtn = null;

    // State
    let currentAttachment = null;
    let isProcessing = false;
    let chatHistory = [];

    // Initialize on DOMContentLoaded or immediately if already loaded
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initJarvis);
    } else {
        initJarvis();
    }

    function initJarvis() {
        triggerWrap = document.getElementById("jarvisTriggerWrap");
        openBtn = document.getElementById("jarvisOpenBtn");
        chatPanel = document.getElementById("jarvisChatPanel");
        closeBtn = document.getElementById("jarvisCloseBtn");
        contactBtn = document.getElementById("jarvisContactBtn");
        contactModal = document.getElementById("jarvisContactModal");
        contactModalClose = document.getElementById("jarvisContactModalClose");
        contactModalBackdrop = document.getElementById("jarvisContactModalBackdrop");
        messagesList = document.getElementById("jarvisMessagesList");
        typingRow = document.getElementById("jarvisTyping");
        form = document.getElementById("jarvisForm");
        textarea = document.getElementById("jarvisInput");
        sendBtn = document.getElementById("jarvisSendBtn");
        attachBtn = document.getElementById("jarvisAttachBtn");
        fileInput = document.getElementById("jarvisFileInput");
        attachmentPreview = document.getElementById("jarvisAttachmentPreview");
        attachmentNameEl = document.getElementById("jarvisAttachmentName");
        attachmentRemoveBtn = document.getElementById("jarvisAttachmentRemove");

        if (!openBtn || !chatPanel) return;

        // Event Listeners
        openBtn.addEventListener("click", openChat);
        if (closeBtn) closeBtn.addEventListener("click", closeChat);

        // Escape Key to close panel or modal
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                if (contactModal && contactModal.style.display !== "none") {
                    closeContactModal();
                } else if (chatPanel.style.display !== "none") {
                    closeChat();
                }
            }
        });

        // Contact Us Button - Open Contact Modal
        if (contactBtn) {
            contactBtn.addEventListener("click", function (e) {
                e.preventDefault();
                openContactModal();
            });
        }

        // Contact Modal Close Button & Backdrop
        if (contactModalClose) {
            contactModalClose.addEventListener("click", closeContactModal);
        }
        if (contactModalBackdrop) {
            contactModalBackdrop.addEventListener("click", closeContactModal);
        }

        // Quick Suggestion Chips
        document.addEventListener("click", function (e) {
            const chip = e.target.closest(".jarvis-chip");
            if (chip && !isProcessing) {
                const query = chip.getAttribute("data-query") || chip.textContent.trim();
                sendMessage(query);
            }
        });

        // Form Submit / Send Button
        if (sendBtn) {
            sendBtn.addEventListener("click", function () {
                if (textarea && !isProcessing) {
                    const text = textarea.value.trim();
                    if (text || currentAttachment) {
                        sendMessage(text);
                    }
                }
            });
        }

        // Textarea Keyboard Handling (Enter to Send, Shift+Enter for Newline)
        if (textarea) {
            textarea.addEventListener("keydown", function (e) {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    if (!isProcessing) {
                        const text = textarea.value.trim();
                        if (text || currentAttachment) {
                            sendMessage(text);
                        }
                    }
                }
            });

            // Auto-expanding textarea
            textarea.addEventListener("input", function () {
                this.style.height = "auto";
                this.style.height = Math.min(this.scrollHeight, 100) + "px";
            });
        }

        // File Attachment Handling
        if (attachBtn && fileInput) {
            attachBtn.addEventListener("click", function () {
                fileInput.click();
            });

            fileInput.addEventListener("change", function () {
                if (this.files && this.files.length > 0) {
                    const file = this.files[0];
                    // Validate 5MB limit
                    if (file.size > 5 * 1024 * 1024) {
                        alert("Attachment exceeds the 5MB size limit.");
                        this.value = "";
                        return;
                    }
                    currentAttachment = file;
                    if (attachmentNameEl) attachmentNameEl.textContent = file.name;
                    if (attachmentPreview) attachmentPreview.style.display = "block";
                }
            });
        }

        if (attachmentRemoveBtn) {
            attachmentRemoveBtn.addEventListener("click", clearAttachment);
        }

        // Check if chat was open previously in this session
        if (sessionStorage.getItem("jarvis_open") === "true") {
            openChat(false);
        }
    }

    function openChat(saveState = true) {
        if (!chatPanel) return;
        chatPanel.style.display = "flex";
        chatPanel.classList.remove("is-closing");
        chatPanel.setAttribute("aria-hidden", "false");
        if (triggerWrap) {
            triggerWrap.classList.add("is-hidden");
            triggerWrap.style.display = "none";
        }
        if (saveState) sessionStorage.setItem("jarvis_open", "true");
        if (textarea) textarea.focus();
        scrollToBottom();
    }

    function closeChat() {
        if (!chatPanel) return;
        chatPanel.classList.add("is-closing");
        chatPanel.setAttribute("aria-hidden", "true");
        sessionStorage.setItem("jarvis_open", "false");
        setTimeout(function () {
            chatPanel.style.display = "none";
            chatPanel.classList.remove("is-closing");
            if (triggerWrap) {
                triggerWrap.style.display = "flex";
                triggerWrap.classList.remove("is-hidden");
            }
            if (openBtn) openBtn.focus();
        }, 200);
    }

    function openContactModal() {
        if (contactModal) {
            contactModal.style.display = "flex";
            contactModal.setAttribute("aria-hidden", "false");
        }
    }

    function closeContactModal() {
        if (contactModal) {
            contactModal.style.display = "none";
            contactModal.setAttribute("aria-hidden", "true");
        }
    }

    function clearAttachment() {
        currentAttachment = null;
        if (fileInput) fileInput.value = "";
        if (attachmentPreview) attachmentPreview.style.display = "none";
    }

    function extractPageContext() {
        const path = window.location.pathname.toLowerCase();
        const urlParams = new URLSearchParams(window.location.search);
        
        let page = "home";
        let courseId = urlParams.get("course") || "";
        let topicId = urlParams.get("topic") || "";
        let jobId = urlParams.get("job_id") || "";

        if (path.includes("/course")) {
            page = "courses";
            // Check if course ID is in path e.g. /courses/ai-engineer
            const match = path.match(/\/courses\/([^\/\?]+)/);
            if (match) {
                page = "course_detail";
                courseId = match[1];
            }
        } else if (path.includes("/jobs") || path.includes("/jobs-home")) {
            page = "jobs";
        } else if (path.includes("/results")) {
            page = "results";
        } else if (path.includes("/saved-jobs")) {
            page = "saved_jobs";
        } else if (path.includes("/applied-jobs")) {
            page = "applied_jobs";
        } else if (path.includes("/ats-score")) {
            page = "ats_score";
        } else if (path.includes("/profile")) {
            page = "profile";
        } else if (path.includes("/setup")) {
            page = "setup";
        } else if (path.includes("/login")) {
            page = "login";
        } else if (path.includes("/register")) {
            page = "register";
        }

        return {
            page: page,
            course_id: courseId,
            topic_id: topicId,
            job_id: jobId,
        };
    }

    function formatTime(date) {
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function scrollToBottom() {
        if (messagesList) {
            messagesList.scrollTop = messagesList.scrollHeight;
        }
    }

    // Markdown Formatter for Safe Clean HTML Output
    function renderMarkdown(md) {
        if (!md) return "";
        let html = md;

        // Escape HTML entities to prevent XSS
        html = html
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");

        // Code blocks: ```lang ... ```
        html = html.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, function (match, lang, code) {
            return `<pre><code class="lang-${lang}">${code.trim()}</code></pre>`;
        });

        // Inline code: `code`
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

        // Bold: **text** or __text__
        html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");

        // Italic: *text* or _text_
        html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");

        // Headers: ### Header
        html = html.replace(/^### (.*$)/gim, "<h3>$1</h3>");
        html = html.replace(/^## (.*$)/gim, "<h3>$1</h3>");
        html = html.replace(/^# (.*$)/gim, "<h3>$1</h3>");

        // Markdown Links: [text](url)
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (match, text, url) {
            const isExternal = url.startsWith("http://") || url.startsWith("https://");
            const target = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
            return `<a href="${url}"${target}>${text}</a>`;
        });

        // Unordered lists: - item or * item
        html = html.replace(/^\s*[-*]\s+(.*)$/gim, "<li>$1</li>");
        html = html.replace(/(<li>.*<\/li>)/gis, "<ul>$1</ul>");
        // Fix nested ul duplicates
        html = html.replace(/<\/ul>\s*<ul>/g, "");

        // Convert double newlines to paragraphs
        const paragraphs = html.split(/\n\s*\n/);
        html = paragraphs.map(p => {
            p = p.trim();
            if (!p) return "";
            if (p.startsWith("<h3>") || p.startsWith("<ul>") || p.startsWith("<pre>")) {
                return p;
            }
            return `<p>${p.replace(/\n/g, "<br>")}</p>`;
        }).join("");

        return html;
    }

    function appendUserMessage(text, attachmentName) {
        if (!messagesList) return;
        const now = new Date();
        const row = document.createElement("div");
        row.className = "jarvis-msg-row jarvis-msg-user";

        let contentHtml = "";
        if (attachmentName) {
            contentHtml += `<div style="font-size: 11.5px; opacity: 0.9; margin-bottom: 4px; display: flex; align-items: center; gap: 4px;">📎 <span>${attachmentName}</span></div>`;
        }
        if (text) {
            contentHtml += `<p>${text.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>")}</p>`;
        }

        row.innerHTML = `
            <div class="jarvis-msg-content">
                <div class="jarvis-bubble">${contentHtml}</div>
                <span class="jarvis-msg-time">${formatTime(now)}</span>
            </div>
        `;
        messagesList.appendChild(row);
        scrollToBottom();
    }

    function appendAssistantMessage(replyText) {
        if (!messagesList) return;
        const now = new Date();
        const row = document.createElement("div");
        row.className = "jarvis-msg-row jarvis-msg-assistant";

        const botImgUrl = window.SKILLBRIDGE_BOT_ICON_URL || "/static/images/skillbridge-icon.png";
        const formattedHtml = renderMarkdown(replyText);

        row.innerHTML = `
            <div class="jarvis-msg-avatar">
                <img src="${botImgUrl}" alt="Jarvis">
            </div>
            <div class="jarvis-msg-content">
                <div class="jarvis-bubble">${formattedHtml}</div>
                <span class="jarvis-msg-time">${formatTime(now)}</span>
            </div>
        `;
        messagesList.appendChild(row);
        scrollToBottom();
    }

    function setTyping(loading) {
        isProcessing = loading;
        if (typingRow) {
            typingRow.style.display = loading ? "flex" : "none";
        }
        if (sendBtn) sendBtn.disabled = loading;
        if (textarea) textarea.disabled = loading;
        if (loading) scrollToBottom();
    }

    async function sendMessage(messageText) {
        const text = (messageText || "").trim();
        const file = currentAttachment;

        if (!text && !file) return;

        // Append user bubble to UI
        const attachmentName = file ? file.name : null;
        appendUserMessage(text, attachmentName);

        // Reset inputs
        if (textarea) {
            textarea.value = "";
            textarea.style.height = "auto";
        }
        clearAttachment();

        // Show typing indicator
        setTyping(true);

        const pageContext = extractPageContext();

        try {
            let response;
            if (file) {
                // Send as multipart/form-data
                const formData = new FormData();
                formData.append("message", text);
                formData.append("page", pageContext.page);
                formData.append("course_id", pageContext.course_id);
                formData.append("topic_id", pageContext.topic_id);
                formData.append("job_id", pageContext.job_id);
                formData.append("attachment", file);

                response = await fetch("/api/ai/chat", {
                    method: "POST",
                    body: formData,
                });
            } else {
                // Send as JSON
                response = await fetch("/api/ai/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        message: text,
                        page: pageContext.page,
                        course_id: pageContext.course_id,
                        topic_id: pageContext.topic_id,
                        job_id: pageContext.job_id,
                    }),
                });
            }

            const data = await response.json();
            setTyping(false);

            if (data && data.success && data.reply) {
                appendAssistantMessage(data.reply);
            } else {
                const err = (data && data.error) || "I couldn't retrieve that information right now. Please try again or contact support.";
                appendAssistantMessage(err);
            }
        } catch (err) {
            console.error("Jarvis Chat Error:", err);
            setTyping(false);
            if (typeof navigator !== "undefined" && navigator.onLine === false) {
                appendAssistantMessage("You appear to be offline. Please check your internet connection.");
            } else if (err && err.name === "AbortError") {
                appendAssistantMessage("Jarvis took longer than usual to respond. Please send your question again.");
            } else {
                appendAssistantMessage("Jarvis is momentarily busy or waking up. Please try sending your message again in a moment.");
            }
        }
    }

    // Expose global controller if needed
    window.SkillBridgeJarvis = {
        open: openChat,
        close: closeChat,
        send: sendMessage,
    };
})();
